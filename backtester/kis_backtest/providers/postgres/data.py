"""PostgreSQL에서 OHLCV 과거 데이터를 조회하는 공급자.

테이블명과 컬럼명은 환경변수로 설정한다. SQL 값은 파라미터 바인딩하고,
식별자(테이블/컬럼)는 허용된 이름 형식인지 검증한 뒤 SQL에 넣는다.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, time

import psycopg

from ...models import Bar, Resolution


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_identifier(identifier: str) -> str:
    """일반적인 PostgreSQL 식별자 하나를 검증하고 따옴표로 감싼다."""
    if not _IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(
            f"잘못된 PostgreSQL 컬럼명: {identifier!r}. "
            "영문자/숫자/밑줄로 구성된 일반 식별자를 사용하세요."
        )
    return f'"{identifier}"'


def _quote_table_name(table_name: str) -> str:
    """schema.table 형식의 테이블명을 검증하고 안전하게 감싼다."""
    parts = table_name.split(".")
    if len(parts) not in (1, 2):
        raise ValueError(
            "PG_OHLCV_TABLE은 table_name 또는 schema.table_name 형식이어야 합니다."
        )
    return ".".join(_quote_identifier(part) for part in parts)


def _env_column(name: str, default: str) -> str:
    """환경변수에서 컬럼명을 읽고 검증한다."""
    return _quote_identifier(os.getenv(name, default).strip())


class PostgresDataProvider:
    """PostgreSQL의 public.ohlcv 테이블에서 일봉 데이터를 조회한다.

    기본 스키마:
        public.ohlcv(
            ticker_code, trade_date, open, high, low, close, adj_close, volume
        )

    현재는 open/high/low/close/volume을 사용한다.
    adj_close는 조정종가만 제공하므로 OHLC와 섞어 쓰지 않는다.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or os.getenv("DATABASE_URL")
        if not self._dsn:
            raise ValueError(
                "PostgreSQL 접속 주소가 없습니다. DATABASE_URL을 설정하세요."
            )

        self._table = _quote_table_name(
            os.getenv("PG_OHLCV_TABLE", "public.ohlcv").strip()
        )
        self._symbol_col = _env_column("PG_SYMBOL_COLUMN", "ticker_code")
        self._date_col = _env_column("PG_DATE_COLUMN", "trade_date")
        self._open_col = _env_column("PG_OPEN_COLUMN", "open")
        self._high_col = _env_column("PG_HIGH_COLUMN", "high")
        self._low_col = _env_column("PG_LOW_COLUMN", "low")
        self._close_col = _env_column("PG_CLOSE_COLUMN", "close")
        self._volume_col = _env_column("PG_VOLUME_COLUMN", "volume")

    def get_history(
        self,
        symbol: str,
        start: date,
        end: date,
        resolution: Resolution = Resolution.DAILY,
    ) -> list[Bar]:
        """요청한 종목과 날짜 범위의 OHLCV 데이터를 오름차순으로 반환한다."""
        if resolution != Resolution.DAILY:
            raise NotImplementedError(
                "PostgresDataProvider는 현재 일봉(Resolution.DAILY)만 지원합니다."
            )
        if not symbol or not symbol.strip():
            raise ValueError("종목코드(symbol)는 비어 있을 수 없습니다.")
        if start > end:
            raise ValueError("시작일(start)은 종료일(end)보다 늦을 수 없습니다.")

        sql = f"""
            SELECT
                {self._date_col},
                {self._open_col},
                {self._high_col},
                {self._low_col},
                {self._close_col},
                {self._volume_col}
            FROM {self._table}
            WHERE {self._symbol_col} = %s
              AND {self._date_col} >= %s
              AND {self._date_col} <= %s
            ORDER BY {self._date_col} ASC
        """

        with psycopg.connect(self._dsn, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, (symbol.strip(), start, end))
                rows = cursor.fetchall()

        bars: list[Bar] = []
        for row in rows:
            trade_date, open_price, high, low, close, volume = row

            # 가격 값이 비어 있는 행은 건너뛴다.
            if any(value is None for value in (
                trade_date, open_price, high, low, close
            )):
                continue

            if isinstance(trade_date, datetime):
                bar_time = trade_date
            elif isinstance(trade_date, date):
                bar_time = datetime.combine(trade_date, time.min)
            else:
                raise TypeError(
                    "거래일 컬럼은 PostgreSQL DATE 또는 TIMESTAMP 형식이어야 합니다."
                )

            try:
                bars.append(
                    Bar(
                        time=bar_time,
                        open=float(open_price),
                        high=float(high),
                        low=float(low),
                        close=float(close),
                        volume=int(volume or 0),
                    )
                )
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(
                    f"OHLCV 행을 Bar로 변환하지 못했습니다: 날짜={trade_date!r}"
                ) from exc

        return bars

    def get_close_panel(
        self,
        symbols: list[str] | None,
        start: date,
        end: date,
    ) -> "pd.DataFrame":
        """여러 종목의 종가를 한 번의 쿼리로 읽어 날짜×종목 패널로 돌려준다 (팩터 계산용).

        - symbols가 비어 있으면 테이블의 모든 종목 (현재 존재하는 종목만 → 생존 편향 주의)
        - 수정종가(PG_ADJ_CLOSE_COLUMN, 기본 adj_close)가 기간 내 빠짐없이 있는 종목은 수정종가,
          하나라도 비어 있으면 그 종목은 종가를 쓴다 (한 종목 안에서 두 기준을 섞지 않음).
          PG_ADJ_CLOSE_COLUMN을 빈 값으로 두면 종가만 쓴다.
        """
        import pandas as pd

        adj_name = os.getenv("PG_ADJ_CLOSE_COLUMN", "adj_close").strip()
        adj_col = _quote_identifier(adj_name) if adj_name else "NULL"
        where_symbols = f"AND {self._symbol_col} = ANY(%s)" if symbols else ""
        sql = f"""
            SELECT {self._symbol_col}, {self._date_col}, {self._close_col}, {adj_col}
            FROM {self._table}
            WHERE {self._date_col} >= %s
              AND {self._date_col} <= %s
              {where_symbols}
        """
        params: list = [start, end]
        if symbols:
            params.append([s.strip() for s in symbols if s.strip()])

        with psycopg.connect(self._dsn, connect_timeout=10) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                rows = cursor.fetchall()

        if not rows:
            return pd.DataFrame()
        frame = pd.DataFrame(rows, columns=["symbol", "date", "close", "adj_close"])
        frame["date"] = pd.to_datetime(frame["date"])
        frame[["close", "adj_close"]] = frame[["close", "adj_close"]].astype(float)
        close = frame.pivot_table(index="date", columns="symbol", values="close", aggfunc="last", dropna=False)
        adj = frame.pivot_table(index="date", columns="symbol", values="adj_close", aggfunc="last", dropna=False)
        adj = adj.reindex(index=close.index, columns=close.columns)
        complete = (adj.notna() | close.isna()).all(axis=0)
        panel = close.copy()
        panel.loc[:, complete] = adj.loc[:, complete]
        return panel.where(panel > 0).sort_index()
