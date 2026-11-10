"""Backtest API Routes.

Lean Docker 기반 백테스트 실행.
"""

import logging
import math
import os
from time import perf_counter
from typing import Any, Dict, List, Literal, Optional
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.schemas.backtest import (
    BacktestRequest,
    BacktestResponse,
    BarMinutes,
)
from backend.state import KIS_AUTH_ERRORS
from kis_backtest.errors import BOUNDARY_ERRORS
from kis_backtest.exceptions import LeanError
from kis_backtest.strategies.registry import StrategyRegistry
from kis_backtest.codegen.generator import BAR_MINUTES_CHOICES, LeanCodeGenerator, CodeGenConfig
from kis_backtest.lean.executor import LeanExecutor, LeanRun
from kis_backtest.lean.project_manager import LeanProjectManager
from kis_backtest.lean.data_converter import DataConverter
from kis_backtest.lean.result_formatter import parse_lean_value
import kis_backtest.strategies.preset  # 전략 자동 등록

# KIS 데이터 다운로드/저장 중 발생 가능한 예외
# (인증 실패 + 응답 파싱 IndexError + 프로젝트 예외(KISError 등 LeanError 하위))
_KIS_DATA_ERRORS = (*KIS_AUTH_ERRORS, IndexError, LeanError)

# prepare_market_data/prepare_benchmark_data 내부에서 잡지 않는 예외
# (날짜 형식 오류 ValueError, 디렉토리 생성/파일 삭제 실패 OSError)
_DATA_PREP_ERRORS = (OSError, ValueError)

# 전략 YAML 로드·파라미터 치환 실패
_STRATEGY_LOAD_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError)

# 분봉 백테스트 (PostgreSQL 분봉 테이블 PG_MINUTE_TABLE)
# - 봉 예산: 종목 수 × 영업일 × 하루 봉 수가 이 값을 넘으면 거절한다 (환경변수로 조정).
#   실측: LEAN이 커스텀 분봉을 초당 약 3,500봉 처리 → 150만 봉 ≈ 7분.
MINUTE_MAX_BARS = int(os.getenv("MINUTE_BACKTEST_MAX_BARS", "1500000"))
# 분봉 LEAN 실행 제한 시간 (nginx /backtest/api/ 타임아웃 900초보다 짧게)
MINUTE_LEAN_TIMEOUT = int(os.getenv("MINUTE_LEAN_TIMEOUT", "840"))
DAILY_LEAN_TIMEOUT = 600
# 09:00~15:20 연속 매매 분 수 (종가 단일가 봉은 별도 1개)
_CONTINUOUS_SESSION_MINUTES = 380


# 생성 코드는 시간대를 지정하지 않아 LEAN 기본 시간대(뉴욕)로 돌고, 커스텀 데이터의 시각(한국 시각 숫자)도
# 그 시간대로 해석된다. LEAN 결과의 UTC 시각을 뉴욕 시각으로 되돌리면 CSV에 적힌 한국 시각이 그대로 나온다.
_LEAN_ALGORITHM_TZ = ZoneInfo("America/New_York")


def _lean_time_to_data_clock(value: str) -> str:
    """LEAN 주문 시각(UTC, 예: 2025-11-03T15:40:00Z) → 데이터 시각 문자열(2025-11-03T10:40:00)."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return value
    if parsed.tzinfo is None:
        return value
    return parsed.astimezone(_LEAN_ALGORITHM_TZ).strftime("%Y-%m-%dT%H:%M:%S")


def _load_benchmark_curve(
    workspace: Path,
    start_date: str,
    end_date: str,
) -> Optional[Dict[str, float]]:
    """KOSPI 벤치마크 수익률 곡선 로드

    Returns:
        날짜별 수익률 % (시작일 대비). 예: {"2024-01-02": 0.0, "2024-01-03": 1.5, ...}
    """
    csv_path = workspace / "data" / "index" / "krx" / "daily" / "kospi.csv"

    if not csv_path.exists():
        return None

    try:
        # 날짜 범위
        req_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        req_end = datetime.strptime(end_date, "%Y-%m-%d").date()

        # CSV 파싱 (YYYYMMDD,open,high,low,close,volume)
        prices = {}
        with open(csv_path, "r") as f:
            for line in f:
                parts = line.strip().split(",")
                if len(parts) < 5:
                    continue
                try:
                    dt = datetime.strptime(parts[0], "%Y%m%d").date()
                    close = float(parts[4])
                    if req_start <= dt <= req_end:
                        prices[dt] = close
                except (ValueError, IndexError):
                    continue

        if not prices:
            return None

        # 시작일 기준 수익률 계산
        sorted_dates = sorted(prices.keys())
        first_data_date = sorted_dates[0]
        if first_data_date > req_start + timedelta(days=14):
            logging.getLogger(__name__).warning(
                f"[Benchmark] 시작일 데이터 부족: 요청={req_start}, "
                f"실제={first_data_date} → 불완전한 벤치마크 비교 비활성화"
            )
            return None

        base_price = prices[sorted_dates[0]]

        if base_price <= 0:
            return None

        # 데이터가 종료일에 충분히 가깝지 않으면 None 반환
        # (낡은 캐시로 인한 절반짜리 선 방지 — 없는 게 잘리는 것보다 낫다)
        last_data_date = sorted_dates[-1]
        if last_data_date < req_end - timedelta(days=14):
            logging.getLogger(__name__).warning(
                f"[Benchmark] 데이터 종료일 부족: 데이터={last_data_date}, 요청={req_end} "
                f"→ 벤치마크 선 비활성화"
            )
            return None

        curve = {}
        for dt in sorted_dates:
            pct = ((prices[dt] - base_price) / base_price) * 100
            curve[dt.strftime("%Y-%m-%d")] = round(pct, 2)

        return curve

    except (OSError, ValueError) as e:  # 파일 읽기 실패, 날짜/숫자 파싱 실패
        logging.getLogger(__name__).warning(f"벤치마크 로드 실패: {type(e).__name__}")
        return None


# 차트에 겹쳐 그릴 종목 가격선 최대 개수 (응답 크기 제한)
SYMBOL_CURVE_LIMIT = 20


def _read_close(line: str) -> Optional[float]:
    """LEAN CSV 한 줄(YYYYMMDD[HHMM],o,h,l,c,v[,end])에서 종가를 꺼낸다."""
    parts = line.strip().split(",")
    if len(parts) < 5:
        return None
    try:
        return float(parts[4])
    except ValueError:
        return None


def _load_symbol_curves(
    workspace: Path,
    symbols: List[str],
    start_date: str,
    end_date: str,
    resolution: str = "daily",
    bar_minutes: int = 1,
) -> Optional[Dict[str, Dict[str, float]]]:
    """종목별 일별 종가 곡선 {종목: {YYYY-MM-DD: 종가}}.

    LEAN이 실제로 읽은 워크스페이스 CSV에서 읽어 거래 가격과 기준을 맞춘다.
    - 일봉: data/equity/krx/daily/{종목}.csv
    - 분봉: data/equity/krx/minute/{N}m/{종목}/{YYYYMMDD}.csv 의 마지막 봉 종가
    """
    try:
        req_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        req_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return None

    equity_dir = workspace / "data" / "equity" / "krx"
    curves: Dict[str, Dict[str, float]] = {}
    for symbol in symbols[:SYMBOL_CURVE_LIMIT]:
        closes: Dict[str, float] = {}
        try:
            if resolution == "minute":
                day_dir = equity_dir / "minute" / f"{bar_minutes}m" / symbol.lower()
                for day_file in sorted(day_dir.glob("*.csv")):
                    try:
                        day = datetime.strptime(day_file.stem, "%Y%m%d").date()
                    except ValueError:
                        continue
                    if not req_start <= day <= req_end:
                        continue
                    lines = day_file.read_text().strip().splitlines()
                    close = _read_close(lines[-1]) if lines else None
                    if close is not None:
                        closes[day.isoformat()] = close
            else:
                csv_path = equity_dir / "daily" / f"{symbol.lower()}.csv"
                with open(csv_path, "r") as f:
                    for line in f:
                        try:
                            day = datetime.strptime(line[:8], "%Y%m%d").date()
                        except ValueError:
                            continue
                        close = _read_close(line)
                        if req_start <= day <= req_end and close is not None:
                            closes[day.isoformat()] = close
        except OSError as e:
            logger.warning(f"[SymbolCurve] {symbol} 로드 실패: {type(e).__name__}")
            continue
        if closes:
            curves[symbol] = closes

    return curves or None


def _lean_run_to_api_response(
    lean_run: LeanRun,
    strategy_name: str,
    symbols: List[str],
    start_date: str,
    end_date: str,
    initial_capital: float,
    workspace: Optional[Path] = None,
    resolution: str = "daily",
    bar_minutes: int = 1,
    intraday_exit: Optional[str] = None,
    data_warnings: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """LeanRun을 프론트엔드 API 응답 형식으로 변환"""
    if not lean_run.success:
        raise ValueError(lean_run.error or "백테스트 실패")

    result = lean_run.load_result()
    stats = lean_run.get_statistics()

    # 기본 통계
    net_profit_pct = parse_lean_value(stats.get("Net Profit", 0))
    start_equity = parse_lean_value(stats.get("Start Equity", initial_capital))
    end_equity = parse_lean_value(stats.get("End Equity", initial_capital))
    net_profit = end_equity - start_equity

    # 자산 곡선 (date -> value dict)
    equity_curve = {}
    charts = result.get("charts", {})
    strategy_equity = charts.get("Strategy Equity", {})
    series = strategy_equity.get("series", {})
    equity_series = series.get("Equity", {})
    values = equity_series.get("values", [])
    for point in values:
        if isinstance(point, list) and len(point) >= 2:
            try:
                dt = datetime.fromtimestamp(point[0], tz=_LEAN_ALGORITHM_TZ)
                val = point[4] if len(point) > 4 else point[1]
                equity_curve[dt.strftime("%Y-%m-%d")] = float(val)
            except (TypeError, ValueError, OverflowError, OSError):  # 잘못된 timestamp/값 형식
                continue

    # 거래 내역
    orders = result.get("orders", {})
    if isinstance(orders, dict):
        orders = list(orders.values())
    trades = []
    for order in orders:
        if order.get("status") != 3:  # Filled only
            continue
        symbol_data = order.get("symbol", {})
        symbol_code = symbol_data.get("value", "") if isinstance(symbol_data, dict) else str(symbol_data)
        direction = order.get("direction", 0)
        trades.append({
            "symbol": symbol_code.upper(),
            "direction": "Buy" if direction == 0 else "Sell",
            "quantity": abs(order.get("quantity", 0)),
            "price": order.get("price", 0),
            "time": _lean_time_to_data_clock(order.get("time", "")),
        })

    return {
        "run_id": lean_run.project.run_id,
        "strategy_name": strategy_name,
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "resolution": resolution,
        "bar_minutes": bar_minutes if resolution == "minute" else None,
        "intraday_exit": intraday_exit if resolution == "minute" else None,
        "data_warnings": data_warnings or [],
        "final_capital": end_equity,
        "net_profit": net_profit,
        "net_profit_percent": net_profit_pct,
        "metrics": {
            "basic": {
                "total_return": net_profit_pct,
                "annual_return": parse_lean_value(stats.get("Compounding Annual Return", 0)),
                "max_drawdown": parse_lean_value(stats.get("Drawdown", 0)),
                "start_equity": start_equity,
                "end_equity": end_equity,
            },
            "risk": {
                "sharpe_ratio": parse_lean_value(stats.get("Sharpe Ratio", 0)),
                "sortino_ratio": parse_lean_value(stats.get("Sortino Ratio", 0)),
                "probabilistic_sharpe": parse_lean_value(stats.get("Probabilistic Sharpe Ratio", 0)),
            },
            "greeks": {
                "alpha": parse_lean_value(stats.get("Alpha", 0)),
                "beta": parse_lean_value(stats.get("Beta", 0)),
            },
            "volatility": {
                "annual_std_dev": parse_lean_value(stats.get("Annual Standard Deviation", 0)),
                "annual_variance": parse_lean_value(stats.get("Annual Variance", 0)),
            },
            "benchmark": {
                "information_ratio": parse_lean_value(stats.get("Information Ratio", 0)),
                "tracking_error": parse_lean_value(stats.get("Tracking Error", 0)),
                "treynor_ratio": parse_lean_value(stats.get("Treynor Ratio", 0)),
            },
            "trading": {
                "total_orders": int(parse_lean_value(stats.get("Total Orders", 0))),
                "win_rate": parse_lean_value(stats.get("Win Rate", 0)),
                "loss_rate": parse_lean_value(stats.get("Loss Rate", 0)),
                "avg_win": parse_lean_value(stats.get("Average Win", 0)),
                "avg_loss": parse_lean_value(stats.get("Average Loss", 0)),
                "profit_loss_ratio": parse_lean_value(stats.get("Profit-Loss Ratio", 0)),
                "expectancy": parse_lean_value(stats.get("Expectancy", 0)),
            },
            "other": {
                "total_fees": parse_lean_value(stats.get("Total Fees", 0)),
                "portfolio_turnover": parse_lean_value(stats.get("Portfolio Turnover", 0)),
                "drawdown_recovery": parse_lean_value(stats.get("Drawdown Recovery", 0)),
            },
        },
        "equity_curve": equity_curve,
        "benchmark_curve": _load_benchmark_curve(workspace, start_date, end_date) if workspace else None,
        "symbol_curves": (
            _load_symbol_curves(workspace, symbols, start_date, end_date, resolution, bar_minutes)
            if workspace else None
        ),
        "trades_count": len(trades),
        "trades": trades,
    }

logger = logging.getLogger(__name__)
router = APIRouter()


def _classify_lean_error(error: str, output: str) -> str:
    """Lean 에러 메시지를 사용자 친화적으로 분류

    Args:
        error: stderr 내용
        output: stdout 내용

    Returns:
        사용자 친화적 에러 메시지
    """
    combined = f"{error} {output}".lower()

    # 데이터 관련
    if "no data" in combined or "data not found" in combined:
        return "종목 데이터가 없습니다. 종목 코드를 확인하거나 다른 기간을 선택해주세요."

    if "invalid symbol" in combined or "unknown symbol" in combined:
        return "잘못된 종목 코드입니다. 종목 코드를 확인해주세요."

    # 지표 관련
    if "indicator" in combined and ("not found" in combined or "undefined" in combined):
        return "지표 초기화 실패. 전략의 지표 설정을 확인해주세요."

    if "update" in combined and "tradebar" in combined:
        return "지표 업데이트 오류. 해당 지표는 TradeBar 데이터가 필요합니다."

    # Python 문법
    if "syntaxerror" in combined or "indentationerror" in combined:
        return "전략 코드 생성 오류. 전략 정의를 확인해주세요."

    if "nameerror" in combined or "attributeerror" in combined:
        return f"전략 실행 오류: 정의되지 않은 변수나 속성 참조. ({error[:100]})"

    # 메모리/타임아웃
    if "timeout" in combined or "타임아웃" in combined:
        return "백테스트 시간 초과. 기간을 줄이거나 전략을 단순화해주세요."

    if "memory" in combined or "out of memory" in combined:
        return "메모리 부족. 백테스트 기간을 줄여주세요."

    # Docker 관련
    if "docker" in combined and ("daemon" in combined or "not running" in combined):
        return "Docker가 실행되지 않습니다. Docker Desktop을 시작해주세요."

    # 일반 에러
    if len(error) > 200:
        return f"백테스트 실패: {error[:200]}..."

    return f"백테스트 실패: {error}"


async def prepare_market_data(
    symbols: List[str],
    start_date: str,
    end_date: str,
    workspace: Path,
) -> dict:
    """PostgreSQL의 OHLCV를 조회해 LEAN용 일봉 CSV 파일을 준비한다.

    PostgreSQL이 원본 데이터다. 기존 CSV가 있더라도 날짜 범위와 관계없이
    DB를 다시 조회해 최신 DB 데이터를 반영한다.

    Returns:
        {"downloaded": [...], "skipped": [], "errors": [...]}
    """
    from psycopg import Error as PostgresError
    from kis_backtest.providers.postgres import PostgresDataProvider

    output_dir = workspace / "data" / "equity" / "krx" / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = {"downloaded": [], "skipped": [], "errors": []}

    # API 입력 날짜를 Python date 형식으로 변환
    req_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    req_end = datetime.strptime(end_date, "%Y-%m-%d").date()

    # 연결 정보와 테이블/컬럼 설정은 backtester/.env에서 읽는다.
    provider = PostgresDataProvider()

    for idx, symbol in enumerate(symbols, start=1):
        csv_path = output_dir / f"{symbol.lower()}.csv"

        try:
            logger.info(
                f"[PostgreSQL] 일봉 조회 중 ({idx}/{len(symbols)}): {symbol}"
            )
            bars = provider.get_history(symbol, req_start, req_end)

            if not bars:
                # 예전 CSV가 남아 있으면 데이터가 있는 것처럼 오인할 수 있어 제거한다.
                csv_path.unlink(missing_ok=True)
                result["errors"].append({
                    "symbol": symbol,
                    "error": (
                        f"PostgreSQL에 요청 기간의 OHLCV 데이터가 없습니다: "
                        f"{start_date} ~ {end_date}"
                    ),
                })
                logger.warning(f"[PostgreSQL] 데이터 없음: {symbol}")
                continue

            # LEAN의 기존 파일 기반 데이터 피드가 읽을 수 있는 형식으로 저장한다.
            DataConverter.bars_to_lean_csv(bars, symbol, output_dir)
            result["downloaded"].append(symbol)
            logger.info(
                f"[PostgreSQL] 조회 완료 ({idx}/{len(symbols)}): "
                f"{symbol}, {len(bars)} bars"
            )

        except PostgresError as exc:
            # DB 자체 오류가 나면 같은 오류로 나머지 종목을 반복 조회하지 않는다.
            logger.exception(
                f"[PostgreSQL] 조회 오류: {symbol} ({type(exc).__name__})"
            )
            for remaining_symbol in symbols[idx - 1:]:
                result["errors"].append({
                    "symbol": remaining_symbol,
                    "error": (
                        "PostgreSQL 조회 오류: "
                        f"{type(exc).__name__}. DB 연결, 권한, 테이블/컬럼 설정을 확인하세요."
                    ),
                })
            break
        except (OSError, ValueError, TypeError, OverflowError) as exc:
            logger.exception(
                f"[PostgreSQL] 데이터 변환/저장 실패: "
                f"{symbol} ({type(exc).__name__})"
            )
            result["errors"].append({
                "symbol": symbol,
                "error": f"데이터 변환/저장 실패: {type(exc).__name__}",
            })

    return result


def _parse_request_dates(start_date: str, end_date: str) -> tuple[date, date]:
    try:
        req_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        req_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜는 YYYY-MM-DD 형식이어야 합니다.")
    if req_start > req_end:
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 늦을 수 없습니다.")
    return req_start, req_end


def bars_per_day(bar_minutes: int) -> int:
    """하루 정규장의 N분봉 개수 (연속 매매 구간 + 종가 단일가 봉 1개)."""
    return math.ceil(_CONTINUOUS_SESSION_MINUTES / bar_minutes) + 1


def minute_warmup_start(trading_days: List[date], start: date, warmup_bars: int, bar_minutes: int) -> date:
    """분봉 워밍업 시작일: 시작일 이전 거래일 중 지표 준비에 필요한 거래일 수(+1일 여유)만큼 거슬러 간 날.

    trading_days 는 분봉이 있는 날 목록. 이전 거래일이 모자라면 있는 만큼만 거슬러 간다.
    """
    if warmup_bars <= 0:
        return start
    needed = math.ceil(warmup_bars / bars_per_day(bar_minutes)) + 1
    prior = [day for day in trading_days if day < start]
    if not prior:
        return start
    return prior[-min(needed, len(prior))]


def estimate_minute_bars(symbol_count: int, start: date, end: date, bar_minutes: int) -> int:
    """분봉 백테스트가 LEAN에서 처리할 봉 수 추정 (영업일 기준, 휴장일 미반영이라 약간 큼)."""
    weekdays = sum(
        1 for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    )
    return symbol_count * weekdays * bars_per_day(bar_minutes)


def validate_minute_range(
    start_date: str,
    end_date: str,
    symbol_count: int,
    bar_minutes: int,
    coverage: Optional[tuple[date, date]],
) -> tuple[date, date, list[str]]:
    """분봉 백테스트 기간·규모 검증. 문제가 있으면 400, 진행 가능하면 (시작일, 종료일, 경고)."""
    req_start, req_end = _parse_request_dates(start_date, end_date)
    if req_end > date.today():
        raise HTTPException(status_code=400, detail="분봉 백테스트 종료일은 오늘 이후일 수 없습니다.")
    if coverage is None:
        raise HTTPException(
            status_code=400,
            detail="PostgreSQL에 분봉 데이터가 없습니다. PG_MINUTE_TABLE 설정을 확인하세요.",
        )
    earliest, latest = coverage
    if req_start < earliest:
        raise HTTPException(
            status_code=400,
            detail=f"DB 분봉은 {earliest.isoformat()}부터 있습니다. 시작일을 {earliest.isoformat()} 이후로 설정하세요.",
        )
    if req_start > latest:
        raise HTTPException(
            status_code=400,
            detail=f"DB 분봉은 {latest.isoformat()}까지 있습니다 (매일 장 마감 후 수집). 시작일을 그 이전으로 설정하세요.",
        )

    warnings = []
    if req_end > latest:
        warnings.append(
            f"{latest.isoformat()} 이후 분봉은 아직 DB에 없습니다 (매일 장 마감 후 수집). "
            f"결과는 {latest.isoformat()}까지의 데이터로 계산했습니다."
        )

    estimated = estimate_minute_bars(symbol_count, req_start, min(req_end, latest), bar_minutes)
    if estimated > MINUTE_MAX_BARS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"분봉 백테스트 규모가 너무 큽니다: 예상 {estimated:,}봉 (한도 {MINUTE_MAX_BARS:,}봉). "
                "봉 주기를 늘리거나 종목 수·기간을 줄이세요."
            ),
        )
    return req_start, req_end, warnings


async def prepare_minute_market_data(
    symbols: List[str],
    start_date: str,
    end_date: str,
    workspace: Path,
    bar_minutes: int = 1,
    warmup_bars: int = 0,
) -> dict:
    """PostgreSQL 분봉 테이블의 1분봉을 N분봉으로 묶어 LEAN 날짜별 CSV로 준비한다.

    파일: data/equity/krx/minute/{N}m/{종목}/{YYYYMMDD}.csv
    - DB가 원본이다. 워밍업 구간(시작일 이전 거래일)부터 종료일까지 매번 다시 읽어 쓴다
      (실측 20종목 × 1년 조회 약 5초).
    - 분봉이 없는 날(주말·휴장·거래정지)은 빈 파일로 둬서 LEAN의 "파일 없음" 경고를 막는다.

    Returns:
        {"downloaded": [...], "skipped": [], "errors": [...], "warnings": [...],
         "warmup_days": 시작일 이전 데이터의 달력 일수, "trading_days": 분봉이 있는 날 목록,
         "source": "PostgreSQL 분봉"}
    """
    from psycopg import Error as PostgresError
    from kis_backtest.providers.postgres import PostgresDataProvider

    result = {
        "downloaded": [], "skipped": [], "errors": [], "warnings": [],
        "warmup_days": 0, "trading_days": [], "source": "PostgreSQL 분봉",
    }

    def db_error(exc: Exception) -> dict:
        logger.exception(f"[PostgreSQL 분봉] 조회 오류 ({type(exc).__name__})")
        result["errors"] = [
            {
                "symbol": symbol,
                "error": (
                    f"PostgreSQL 분봉 조회 오류: {type(exc).__name__}. "
                    "DB 연결, 권한, PG_MINUTE_TABLE 설정을 확인하세요."
                ),
            }
            for symbol in symbols
        ]
        return result

    provider = PostgresDataProvider()
    try:
        coverage = provider.get_minute_coverage()
    except PostgresError as exc:
        return db_error(exc)

    req_start, req_end, warnings = validate_minute_range(
        start_date, end_date, len(symbols), bar_minutes, coverage
    )
    result["warnings"].extend(warnings)

    # 워밍업: 지표 준비에 필요한 거래일 수(+1일 여유). 휴장일을 감안해 달력으로 넉넉히 거슬러 읽는다.
    warmup_trading_days = math.ceil(warmup_bars / bars_per_day(bar_minutes)) + 1 if warmup_bars > 0 else 0
    fetch_start = req_start
    if warmup_trading_days:
        fetch_start = max(coverage[0], req_start - timedelta(days=warmup_trading_days * 7 // 5 + 10))

    try:
        days_by_symbol = provider.get_minute_days(symbols, fetch_start, req_end)
    except PostgresError as exc:
        return db_error(exc)

    trading_days = sorted({
        day for days in days_by_symbol.values() for day, bars in days.items() if bars
    })
    warmup_start = minute_warmup_start(trading_days, req_start, warmup_bars, bar_minutes)
    result["warmup_days"] = (req_start - warmup_start).days
    result["trading_days"] = trading_days

    minute_root = workspace / "data" / "equity" / "krx" / "minute" / f"{bar_minutes}m"
    for symbol in symbols:
        days = days_by_symbol.get(symbol.strip(), {})
        bar_count = sum(len(bars) for day, bars in days.items() if day >= req_start)
        if bar_count == 0:
            result["errors"].append({
                "symbol": symbol,
                "error": (
                    f"DB에 {start_date} ~ {end_date} 분봉이 없습니다 "
                    "(종목코드, 상장일, 거래정지 여부를 확인하세요)."
                ),
            })
            continue

        symbol_dir = minute_root / symbol.lower()
        day = warmup_start
        while day <= req_end:
            bars = days.get(day)
            aggregated = DataConverter.aggregate_minute_bars(bars, bar_minutes) if bars else []
            DataConverter.write_lean_minute_day(aggregated, day, symbol_dir / f"{day:%Y%m%d}.csv")
            day += timedelta(days=1)

        used_days = {day: bars for day, bars in days.items() if day >= warmup_start}
        for gap_day, ratio in DataConverter.find_price_gaps(used_days):
            result["warnings"].append(
                f"{symbol} {gap_day.isoformat()}: 전일 종가 대비 시가 {ratio}배 — 액면분할·병합 등 "
                "가격 불연속이 의심됩니다. DB 분봉은 KIS 수정주가로 매일 맞추지만, 아직 반영되지 않았다면 "
                "이 종목 수익률이 왜곡될 수 있습니다."
            )

        result["downloaded"].append(symbol)
        logger.info(
            f"[PostgreSQL 분봉] 준비 완료: {symbol}, 1분봉 {bar_count}개 → {bar_minutes}분봉"
        )

    return result


async def prepare_backtest_data(
    resolution: str,
    symbols: List[str],
    start_date: str,
    end_date: str,
    workspace: Path,
    bar_minutes: int = 1,
    warmup_bars: int = 0,
) -> dict:
    """해상도에 맞춰 LEAN 입력 데이터를 준비한다 (일봉·분봉 모두 PostgreSQL)."""
    if resolution == "minute":
        return await prepare_minute_market_data(
            symbols, start_date, end_date, workspace,
            bar_minutes=bar_minutes, warmup_bars=warmup_bars,
        )
    _parse_request_dates(start_date, end_date)
    result = await prepare_market_data(symbols, start_date, end_date, workspace)
    result["source"] = "PostgreSQL"
    return result


@router.get(
    "/minute-coverage",
    summary="분봉 데이터 범위",
    description="PostgreSQL 분봉 테이블의 첫·마지막 거래일과 분봉 백테스트 한도.",
)
async def minute_coverage() -> Dict[str, Any]:
    from psycopg import Error as PostgresError
    from kis_backtest.providers.postgres import PostgresDataProvider

    try:
        coverage = PostgresDataProvider().get_minute_coverage()
    except (PostgresError, ValueError) as exc:
        logger.warning(f"[PostgreSQL 분봉] 범위 조회 실패: {type(exc).__name__}")
        raise HTTPException(status_code=503, detail=f"PostgreSQL 분봉 범위 조회 실패: {type(exc).__name__}")
    return {
        "earliest": coverage[0].isoformat() if coverage else None,
        "latest": coverage[1].isoformat() if coverage else None,
        "bar_minutes": list(BAR_MINUTES_CHOICES),
        "bars_per_day": {str(n): bars_per_day(n) for n in BAR_MINUTES_CHOICES},
        "max_bars": MINUTE_MAX_BARS,
    }


async def prepare_benchmark_data(
    start_date: str,
    end_date: str,
    workspace: Path,
    index_code: str = "0001",  # KOSPI
) -> bool:
    """KOSPI 벤치마크 데이터 준비

    Args:
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)
        workspace: Lean 워크스페이스 경로
        index_code: 지수코드 (0001=KOSPI, 1001=KOSDAQ)

    Returns:
        성공 여부
    """
    from kis_backtest.providers.kis.auth import KISAuth
    from kis_backtest.providers.kis.data import KISDataProvider

    # 지수 코드 → 파일명 매핑
    index_names = {"0001": "kospi", "1001": "kosdaq"}
    index_name = index_names.get(index_code, index_code)

    output_dir = workspace / "data" / "index" / "krx" / "daily"
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{index_name}.csv"

    # 요청 날짜 파싱
    req_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    req_end = datetime.strptime(end_date, "%Y-%m-%d").date()

    # 캐시 확인 (시작일 커버리지 필수)
    if csv_path.exists() and csv_path.stat().st_size > 100:
        try:
            with open(csv_path, 'r') as f:
                lines = f.readlines()
                if len(lines) >= 2:
                    first_date_str = lines[0].split(',')[0].strip()
                    last_date_str = lines[-1].split(',')[0].strip()
                    first_date = datetime.strptime(first_date_str, "%Y%m%d").date()
                    last_date = datetime.strptime(last_date_str, "%Y%m%d").date()

                    # 시작일 근처 데이터 필수 (7일 이내)
                    start_tolerance = timedelta(days=7)
                    end_tolerance = timedelta(days=7)

                    # 시작일과 종료일 모두 커버해야 캐시 사용
                    if first_date <= req_start + start_tolerance and last_date >= req_end - end_tolerance:
                        req_days = (req_end - req_start).days
                        if req_days > 0:
                            effective_start = max(req_start, first_date)
                            effective_end = min(req_end, last_date)
                            covered_days = (effective_end - effective_start).days
                            if covered_days / req_days >= 0.85:
                                logger.info(f"[Benchmark] 캐시 사용: {csv_path} (범위: {first_date} ~ {last_date})")
                                return True
                    else:
                        logger.info(f"[Benchmark] 캐시 범위 불일치 - 요청: {req_start}~{req_end}, 캐시: {first_date}~{last_date}")
                        # 낡은 캐시 삭제 — 다운로드 실패 시 절반짜리 데이터가 남지 않도록
                        csv_path.unlink(missing_ok=True)
        except (OSError, ValueError) as e:  # 파일 읽기/삭제 실패, 날짜 파싱 실패
            logger.warning(f"[Benchmark] 캐시 체크 실패: {type(e).__name__}")
            csv_path.unlink(missing_ok=True)  # 손상된 캐시 제거

    # KIS API 다운로드
    try:
        auth = KISAuth.from_env()
        provider = KISDataProvider(auth)

        logger.info(f"[Benchmark] 다운로드 중: {index_name} ({index_code})")
        bars = provider.get_index_history(index_code, req_start, req_end)

        if bars:
            with open(csv_path, "w") as f:
                for bar in bars:
                    f.write(bar.to_lean_csv_line() + "\n")
            logger.info(
                f"[Benchmark] 저장 완료: rows={len(bars)}, "
                f"first={bars[0].time.date()}, last={bars[-1].time.date()}"
            )
            return True
        else:
            logger.warning(f"[Benchmark] 데이터 없음: {index_name}")
            return False

    except _KIS_DATA_ERRORS as e:
        logger.error(f"[Benchmark] 다운로드 실패: {type(e).__name__}")
        return False


@router.post(
    "/run",
    response_model=BacktestResponse,
    summary="백테스트 실행",
    description="전략을 백테스트합니다 (Lean Docker). param_overrides로 파라미터 변경 가능.",
)
async def run_backtest(request: BacktestRequest) -> BacktestResponse:
    """백테스트 실행

    param_overrides 예시:
        {"period": 21, "oversold": 25}
    """
    request_started = perf_counter()

    # 전략 빌드 (param_overrides 적용)
    try:
        if request.param_overrides:
            definition = StrategyRegistry.build_with_params(
                request.strategy_id,
                **request.param_overrides,
            )
        else:
            definition = StrategyRegistry.build(request.strategy_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy not found: {request.strategy_id}"
        )
    
    # 날짜 검증
    start_date = request.start_date
    end_date = request.end_date
    
    if isinstance(start_date, date):
        start_date = start_date.isoformat()
    if isinstance(end_date, date):
        end_date = end_date.isoformat()

    # 코드 생성기 준비 (분봉 워밍업 데이터 범위를 정하려면 지표 기간이 먼저 필요하다)
    try:
        config = CodeGenConfig(
            commission_rate=request.commission_rate or 0.00015,
            tax_rate=request.tax_rate or 0.002,
            slippage=request.slippage or 0.0,
            initial_capital=request.initial_capital,
            resolution=request.resolution,
            bar_minutes=request.bar_minutes,
            intraday_exit=request.intraday_exit,
        )
        generator = LeanCodeGenerator(definition, config=config)
        warmup_bars = generator.warmup_bars()
    except BOUNDARY_ERRORS as e:
        raise HTTPException(status_code=400, detail=f"Code generation failed: {e}")

    # 워크스페이스 경로
    manager = LeanProjectManager()
    workspace = manager.workspace

    # 시장 데이터 준비 (일봉·분봉 모두 PostgreSQL → Lean CSV)
    data_started = perf_counter()
    try:
        data_result = await prepare_backtest_data(
            resolution=request.resolution,
            symbols=request.symbols,
            start_date=start_date,
            end_date=end_date,
            workspace=workspace,
            bar_minutes=request.bar_minutes,
            warmup_bars=warmup_bars,
        )
        logger.info(
            f"[Data] 결과: {data_result['source']} 조회 {len(data_result['downloaded'])}, "
            f"캐시 {len(data_result['skipped'])}, 실패 {len(data_result['errors'])}"
        )
        logger.info(
            f"[Timing] phase=market_data resolution={request.resolution} "
            f"seconds={perf_counter() - data_started:.3f}"
        )
        
        # 데이터 없으면 에러
        if data_result["errors"]:
            first_error = data_result["errors"][0]
            error_msg = first_error["error"]
            failed_symbols = ", ".join(
                item["symbol"] for item in data_result["errors"]
            )
            raise HTTPException(
                status_code=400,
                detail=f"{data_result['source']} 데이터 준비 실패 ({failed_symbols}): {error_msg}",
            )
            
    except HTTPException:
        raise
    except _DATA_PREP_ERRORS as e:
        raise HTTPException(status_code=500, detail=f"데이터 준비 실패: {e}")

    # 벤치마크 데이터 준비 (KOSPI)
    benchmark_started = perf_counter()
    try:
        await prepare_benchmark_data(
            start_date=start_date,
            end_date=end_date,
            workspace=workspace,
        )
    except _DATA_PREP_ERRORS as e:
        logger.warning(f"[Benchmark] 준비 실패 (무시): {type(e).__name__}")
    finally:
        logger.info(
            f"[Timing] phase=kis_benchmark seconds={perf_counter() - benchmark_started:.3f}"
        )

    # Lean 코드 생성
    codegen_started = perf_counter()
    try:
        config.warmup_days = data_result.get("warmup_days", 0)
        code = generator.generate(
            symbols=request.symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=request.initial_capital,
        )
        logger.info(
            f"[Timing] phase=code_generation seconds={perf_counter() - codegen_started:.3f}"
        )
    except BOUNDARY_ERRORS as e:
        raise HTTPException(status_code=400, detail=f"Code generation failed: {e}")

    # Docker 환경 확인
    if not LeanExecutor.check_docker():
        raise HTTPException(
            status_code=503,
            detail="LEAN을 돌리는 로컬 PC의 Docker가 꺼져 있습니다. 그 PC에서 Docker Desktop을 켜면 바로 실행됩니다 (워커 재시작 불필요)."
        )

    if not LeanExecutor.check_image():
        raise HTTPException(
            status_code=503,
            detail="Lean 이미지가 없습니다. 'docker pull quantconnect/lean:latest' 실행 후 재시도해주세요."
        )

    # 프로젝트 생성 및 백테스트 실행
    try:
        project = LeanProjectManager.create_project(
            run_id=LeanProjectManager.generate_run_id("bt"),
            symbols=request.symbols,
            start_date=start_date,
            end_date=end_date,
            initial_capital=request.initial_capital,
            strategy_id=definition.id,
            strategy_name=definition.name,
        )
        # 코드 저장
        project.main_py.write_text(code)

        lean_started = perf_counter()
        lean_run = LeanExecutor.run(
            project,
            timeout=MINUTE_LEAN_TIMEOUT if request.resolution == "minute" else DAILY_LEAN_TIMEOUT,
        )
        logger.info(
            f"[Timing] phase=lean_execution seconds={perf_counter() - lean_started:.3f} "
            f"success={lean_run.success}"
        )

        if lean_run.success:
            result_data = _lean_run_to_api_response(
                lean_run, definition.name, request.symbols,
                start_date, end_date, request.initial_capital,
                workspace=workspace,
                resolution=request.resolution,
                bar_minutes=request.bar_minutes,
                intraday_exit=request.intraday_exit,
                data_warnings=data_result.get("warnings"),
            )
            logger.info(
                f"[Timing] phase=total seconds={perf_counter() - request_started:.3f}"
            )
            return BacktestResponse(
                success=True,
                data=result_data,
                message="백테스트 완료",
            )
        else:
            # 에러 메시지 분류
            error = lean_run.error or "Unknown error"
            error_detail = _classify_lean_error(error, lean_run.output)
            raise HTTPException(status_code=500, detail=error_detail)
    except HTTPException:
        raise
    except BOUNDARY_ERRORS as e:
        raise HTTPException(status_code=500, detail=f"백테스트 실행 오류: {e}")



class CustomBacktestRequest(BaseModel):
    """커스텀 백테스트 요청"""
    yaml_content: str
    symbols: List[str]
    start_date: str
    end_date: str
    initial_capital: float = 100_000_000
    param_overrides: Optional[Dict[str, Any]] = None  # $param_name 오버라이드
    commission_rate: Optional[float] = 0.00015  # 수수료율 (기본 0.015%)
    tax_rate: Optional[float] = 0.002  # 거래세율 (기본 0.2%)
    slippage: Optional[float] = 0.0  # 슬리피지 (기본 0%)
    resolution: Literal["daily", "minute"] = "daily"  # 봉 단위 (둘 다 PostgreSQL)
    bar_minutes: BarMinutes = 1  # 분봉 주기 (minute 전용)
    intraday_exit: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")  # 장마감 청산 HH:MM (minute 전용)


@router.post(
    "/run-custom",
    response_model=BacktestResponse,
    summary="커스텀 전략 백테스트",
    description="YAML 정의로 커스텀 전략을 백테스트합니다. param_overrides로 $param_name 값 변경 가능.",
)
async def run_custom_backtest(request: CustomBacktestRequest) -> BacktestResponse:
    """커스텀 전략 백테스트 - 스키마 기반

    param_overrides로 YAML의 $param_name 값을 오버라이드할 수 있습니다.
    예: {"period": 21, "oversold": 25}
    """
    request_started = perf_counter()
    from kis_backtest.file.loader import StrategyFileLoader

    # YAML 파싱 → StrategySchema (타입 안전, param_overrides 적용)
    try:
        schema = StrategyFileLoader.load_schema_with_params(
            request.yaml_content,
            param_overrides=request.param_overrides,
        )
    except _STRATEGY_LOAD_ERRORS as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")
    if schema is None:
        raise HTTPException(status_code=400, detail="전략 스키마 생성 실패")

    # 코드 생성기 준비 (분봉 워밍업 데이터 범위를 정하려면 지표 기간이 먼저 필요하다)
    try:
        config = CodeGenConfig(
            commission_rate=request.commission_rate or 0.00015,
            tax_rate=request.tax_rate or 0.002,
            slippage=request.slippage or 0.0,
            initial_capital=request.initial_capital,
            resolution=request.resolution,
            bar_minutes=request.bar_minutes,
            intraday_exit=request.intraday_exit,
        )
        generator = LeanCodeGenerator(schema, config=config)
        warmup_bars = generator.warmup_bars()
    except BOUNDARY_ERRORS as e:
        raise HTTPException(status_code=400, detail=f"Code generation failed: {e}")

    # 워크스페이스 경로
    manager = LeanProjectManager()
    workspace = manager.workspace

    # 시장 데이터 준비 (일봉·분봉 모두 PostgreSQL → Lean CSV)
    data_started = perf_counter()
    try:
        data_result = await prepare_backtest_data(
            resolution=request.resolution,
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            workspace=workspace,
            bar_minutes=request.bar_minutes,
            warmup_bars=warmup_bars,
        )
        logger.info(
            f"[Data] 결과: {data_result['source']} 조회 {len(data_result['downloaded'])}, "
            f"캐시 {len(data_result['skipped'])}, 실패 {len(data_result['errors'])}"
        )
        logger.info(
            f"[Timing] phase=market_data resolution={request.resolution} "
            f"seconds={perf_counter() - data_started:.3f}"
        )
        
        # 데이터 없으면 에러
        if data_result["errors"]:
            first_error = data_result["errors"][0]
            error_msg = first_error["error"]
            failed_symbols = ", ".join(
                item["symbol"] for item in data_result["errors"]
            )
            raise HTTPException(
                status_code=400,
                detail=f"{data_result['source']} 데이터 준비 실패 ({failed_symbols}): {error_msg}",
            )
            
    except HTTPException:
        raise
    except _DATA_PREP_ERRORS as e:
        raise HTTPException(status_code=500, detail=f"데이터 준비 실패: {e}")

    # 벤치마크 데이터 준비 (KOSPI)
    benchmark_started = perf_counter()
    try:
        await prepare_benchmark_data(
            start_date=request.start_date,
            end_date=request.end_date,
            workspace=workspace,
        )
    except _DATA_PREP_ERRORS as e:
        logger.warning(f"[Benchmark] 준비 실패 (무시): {type(e).__name__}")
    finally:
        logger.info(
            f"[Timing] phase=kis_benchmark seconds={perf_counter() - benchmark_started:.3f}"
        )

    # Lean 코드 생성 (스키마 기반)
    codegen_started = perf_counter()
    try:
        config.warmup_days = data_result.get("warmup_days", 0)
        code = generator.generate(
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_capital=request.initial_capital,
        )
        logger.info(
            f"[Timing] phase=code_generation seconds={perf_counter() - codegen_started:.3f}"
        )
    except BOUNDARY_ERRORS as e:
        raise HTTPException(status_code=400, detail=f"Code generation failed: {e}")
    
    # Docker 환경 확인
    if not LeanExecutor.check_docker():
        raise HTTPException(
            status_code=503,
            detail="LEAN을 돌리는 로컬 PC의 Docker가 꺼져 있습니다. 그 PC에서 Docker Desktop을 켜면 바로 실행됩니다 (워커 재시작 불필요)."
        )

    if not LeanExecutor.check_image():
        raise HTTPException(
            status_code=503,
            detail="Lean 이미지가 없습니다. 'docker pull quantconnect/lean:latest' 실행 후 재시도해주세요."
        )

    # 프로젝트 생성 및 백테스트 실행
    try:
        project = LeanProjectManager.create_project(
            run_id=LeanProjectManager.generate_run_id("bt_custom"),
            symbols=request.symbols,
            start_date=request.start_date,
            end_date=request.end_date,
            initial_capital=request.initial_capital,
            strategy_id=schema.id,
            strategy_name=schema.name,
        )
        # 코드 저장
        project.main_py.write_text(code)

        lean_started = perf_counter()
        lean_run = LeanExecutor.run(
            project,
            timeout=MINUTE_LEAN_TIMEOUT if request.resolution == "minute" else DAILY_LEAN_TIMEOUT,
        )
        logger.info(
            f"[Timing] phase=lean_execution seconds={perf_counter() - lean_started:.3f} "
            f"success={lean_run.success}"
        )

        if lean_run.success:
            result_data = _lean_run_to_api_response(
                lean_run, schema.name, request.symbols,
                request.start_date, request.end_date, request.initial_capital,
                workspace=workspace,
                resolution=request.resolution,
                bar_minutes=request.bar_minutes,
                intraday_exit=request.intraday_exit,
                data_warnings=data_result.get("warnings"),
            )
            logger.info(
                f"[Timing] phase=total seconds={perf_counter() - request_started:.3f}"
            )
            return BacktestResponse(
                success=True,
                data=result_data,
                message="백테스트 완료",
            )
        else:
            # 에러 메시지 분류
            error = lean_run.error or "Unknown error"
            error_detail = _classify_lean_error(error, lean_run.output)
            raise HTTPException(status_code=500, detail=error_detail)
    except HTTPException:
        raise
    except BOUNDARY_ERRORS as e:
        raise HTTPException(status_code=500, detail=f"백테스트 실행 오류: {e}")
