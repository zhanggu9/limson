"""데이터 변환기 - KIS DataFrame을 Lean CSV 포맷으로 변환

KIS OpenAPI 데이터를 Lean이 읽을 수 있는 CSV 포맷으로 변환.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple, TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from ..models import Bar

logger = logging.getLogger(__name__)

# KRX 정규장 시작(09:00)과 종가 단일가 매매 시작(15:20) — 자정 기준 분.
# 15:20 이후의 마지막 봉만 종가 단일가 봉으로 본다.
_SESSION_OPEN_MINUTE = 9 * 60
_CLOSING_AUCTION_EARLIEST = 15 * 60 + 20


class DataConverter:
    """KIS DataFrame → Lean CSV 변환기"""
    
    @classmethod
    def export(
        cls,
        data_dict: Dict[str, pd.DataFrame],
        output_dir: str,
        market_type: str = "krx",
    ) -> Dict[str, Path]:
        """데이터를 Lean CSV 포맷으로 내보내기
        
        Args:
            data_dict: {symbol: DataFrame} 형태의 데이터
            output_dir: 출력 디렉토리 경로
            market_type: "krx" (국내) 또는 "us" (해외)
        
        Returns:
            {symbol: Path} 형태의 생성된 파일 경로
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        exported = {}
        
        for symbol, df in data_dict.items():
            try:
                csv_path = cls._export_symbol(symbol, df, output_path, market_type)
                exported[symbol] = csv_path
            except (ValueError, TypeError, KeyError, AttributeError, OSError) as e:
                # ValueError: 필수 컬럼 누락/날짜 파싱/NaN 정수 변환, KeyError: 'date' 컬럼 없음,
                # AttributeError: datetime 아닌 컬럼의 .dt 접근, OSError: CSV 쓰기 실패
                logger.error(f"  - {symbol} 변환 실패: {e}")
        
        return exported
    
    @classmethod
    def _export_symbol(
        cls,
        symbol: str,
        df: pd.DataFrame,
        output_dir: Path,
        market_type: str = "krx",
    ) -> Path:
        """단일 종목 데이터 변환
        
        Args:
            symbol: 종목 코드
            df: 데이터프레임
            output_dir: 출력 디렉토리
            market_type: "krx" (국내) 또는 "us" (해외)
        """
        # 컬럼 정규화
        df_out = df.copy()
        
        # 날짜 컬럼 처리
        if 'date' in df_out.columns:
            df_out['date'] = pd.to_datetime(df_out['date'])
        elif df_out.index.name == 'date' or isinstance(df_out.index, pd.DatetimeIndex):
            df_out = df_out.reset_index()
            df_out.columns.values[0] = 'date'
            df_out['date'] = pd.to_datetime(df_out['date'])
        
        # 필수 컬럼 확인
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in df_out.columns:
                raise ValueError(f"필수 컬럼 없음: {col}")
        
        # Lean 포맷으로 변환 (YYYYMMDD,open,high,low,close,volume)
        df_out['date_str'] = df_out['date'].dt.strftime('%Y%m%d')
        
        # 중복 제거 (날짜 기준)
        df_out = df_out.drop_duplicates(subset=['date_str'], keep='first')
        df_out = df_out.sort_values('date_str')
        
        # 가격 포맷: KRX는 정수(원), US는 소수점 2자리(달러)
        if market_type == "krx":
            # 한국 주식: 원 단위 (정수)
            for col in required_cols:
                df_out[col] = df_out[col].astype(float).round(0).astype(int)
        else:
            # 미국 주식: 달러 단위 (소수점 2자리)
            for col in ['open', 'high', 'low', 'close']:
                df_out[col] = df_out[col].astype(float).round(2)
            df_out['volume'] = df_out['volume'].astype(int)
        
        # CSV 출력 (헤더 없음)
        csv_path = output_dir / f"{symbol.lower()}.csv"
        
        df_out[['date_str', 'open', 'high', 'low', 'close', 'volume']].to_csv(
            csv_path,
            index=False,
            header=False,
        )
        
        return csv_path
    
    @classmethod
    def get_date_range(cls, data_dict: Dict[str, pd.DataFrame]) -> tuple:
        """데이터의 공통 날짜 범위 계산"""
        if not data_dict:
            return None, None
        
        min_date = None
        max_date = None
        
        for df in data_dict.values():
            if df.empty:
                continue
            
            if isinstance(df.index, pd.DatetimeIndex):
                dates = df.index
            elif 'date' in df.columns:
                dates = pd.to_datetime(df['date'])
            else:
                continue
            
            df_min = dates.min()
            df_max = dates.max()
            
            if min_date is None or df_min > min_date:
                min_date = df_min
            if max_date is None or df_max < max_date:
                max_date = df_max
        
        return min_date, max_date
    
    @classmethod
    def bars_to_lean_csv(
        cls,
        bars: List["Bar"],
        symbol: str,
        output_dir: Path,
        market_type: str = "krx",
    ) -> Path:
        """Bar 리스트를 Lean CSV로 변환
        
        Args:
            bars: Bar 객체 리스트
            symbol: 종목 코드
            output_dir: 출력 디렉토리
            market_type: "krx" 또는 "us"
        
        Returns:
            생성된 CSV 파일 경로
        """
        if not bars:
            raise ValueError("빈 bars 리스트")
        
        # Bar → DataFrame
        data = []
        for bar in bars:
            data.append({
                'date': bar.time,
                'open': bar.open,
                'high': bar.high,
                'low': bar.low,
                'close': bar.close,
                'volume': bar.volume,
            })
        
        df = pd.DataFrame(data)
        
        # output_dir이 str이면 Path로 변환
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # 변환 실행
        return cls._export_symbol(symbol, df, output_path, market_type)

    @staticmethod
    def aggregate_minute_bars(
        bars: List[Tuple[int, int, int, int, int, int]],
        bar_minutes: int,
    ) -> List[Tuple[int, int, int, int, int, int, int]]:
        """하루치 1분봉을 N분봉으로 묶는다.

        Args:
            bars: [(hhmm, open, high, low, close, volume), ...] 시각 오름차순 (hhmm = 봉 시작 시각)
            bar_minutes: 묶을 분 수 (1이면 그대로)

        Returns:
            [(시작분, 끝분, open, high, low, close, volume), ...] — 분은 자정 기준 (09:00 = 540)

        - 구간은 09:00 기준 N분 단위로 자른다.
        - 그날 마지막 봉이 15:20 이후이고 바로 앞 봉과 떨어져 있으면 종가 단일가 봉으로 보고 따로 둔다
          (수능일처럼 장이 늦게 끝나는 날도 같은 규칙).
        - 봉 끝 시각은 시작 + N분이되 종가 단일가 봉 시작 시각을 넘지 않는다.
          LEAN은 끝 시각에 봉을 전달하므로 전략은 봉이 끝난 뒤에야 그 값을 본다.
        """
        if bar_minutes < 1:
            raise ValueError("bar_minutes는 1 이상이어야 합니다.")
        minutes = [((t // 100) * 60 + t % 100, o, h, lo, c, v) for t, o, h, lo, c, v in bars]

        closing = None
        if minutes and minutes[-1][0] >= _CLOSING_AUCTION_EARLIEST and (
            len(minutes) == 1 or minutes[-1][0] - minutes[-2][0] > 1
        ):
            closing = minutes.pop()

        out: List[list] = []
        for minute, o, h, lo, c, v in minutes:
            start = minute - (minute - _SESSION_OPEN_MINUTE) % bar_minutes
            if out and out[-1][0] == start:
                bucket = out[-1]
                bucket[3] = max(bucket[3], h)
                bucket[4] = min(bucket[4], lo)
                bucket[5] = c
                bucket[6] += v
            else:
                out.append([start, start + bar_minutes, o, h, lo, c, v])

        if closing is not None:
            closing_start = closing[0]
            for bucket in out:
                bucket[1] = min(bucket[1], closing_start)
            out.append([closing_start, closing_start + 1, *closing[1:]])
        return [tuple(bucket) for bucket in out]

    @staticmethod
    def write_lean_minute_day(
        bars: List[Tuple[int, int, int, int, int, int, int]],
        day: date,
        csv_path: Path,
    ) -> Path:
        """aggregate_minute_bars 결과 하루치를 LEAN 분봉 CSV로 저장한다 (빈 리스트면 빈 파일 = 휴장·거래정지).

        한 줄: 시작 YYYYMMDDHHMM,open,high,low,close,volume,끝 YYYYMMDDHHMM (헤더 없음, 원 단위 정수)
        중간에 실패해도 깨진 파일이 남지 않도록 임시 파일에 쓴 뒤 교체한다.
        """
        csv_path = Path(csv_path)
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        stamp = day.strftime("%Y%m%d")
        lines = [
            f"{stamp}{start // 60:02d}{start % 60:02d},{o},{h},{lo},{c},{v},"
            f"{stamp}{end // 60:02d}{end % 60:02d}\n"
            for start, end, o, h, lo, c, v in bars
        ]
        tmp_path = csv_path.with_suffix(".csv.tmp")
        tmp_path.write_text("".join(lines))
        tmp_path.replace(csv_path)
        return csv_path

    @staticmethod
    def find_price_gaps(
        days: Dict[date, List[Tuple[int, int, int, int, int, int]]],
        threshold: float = 0.35,
    ) -> List[Tuple[date, float]]:
        """전 거래일 마지막 종가 대비 다음 거래일 첫 시가가 threshold 넘게 벌어진 날.

        KRX 가격제한폭(±30%)을 넘는 벌어짐은 액면분할·병합 같은 가격 불연속일 가능성이 크다.
        분봉은 수정주가가 아니므로 이런 날 전후를 이어 계산하면 수익률이 왜곡된다.

        Returns:
            [(거래일, 시가/전일종가 비율), ...]
        """
        gaps = []
        prev_close = None
        for day in sorted(days):
            bars = days[day]
            if not bars:
                continue
            first_open = bars[0][1]
            if prev_close and first_open > 0:
                ratio = first_open / prev_close
                if ratio > 1 + threshold or ratio < 1 - threshold:
                    gaps.append((day, round(ratio, 3)))
            prev_close = bars[-1][4]
        return gaps
