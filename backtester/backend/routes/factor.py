"""Factor API Routes.

벡터화 백테스트로 횡단면 팩터(현재: 모멘텀)를 빠르게 검증한다.
LEAN 백테스트와 달리 Docker 없이 PostgreSQL 일봉만으로 몇 초 안에 끝난다.
"""

import logging
from datetime import datetime, timedelta
from time import perf_counter
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException
from psycopg import Error as PostgresError
from pydantic import BaseModel, Field, model_validator

from kis_backtest.providers.postgres import PostgresDataProvider
from kis_backtest.vectorized import CostModel, run_momentum_backtest

logger = logging.getLogger(__name__)
router = APIRouter()

# 거래일 → 달력일 환산 여유 (주말·휴일)
_CALENDAR_DAYS_PER_TRADING_DAY = 1.5


class MomentumFactorRequest(BaseModel):
    """모멘텀 팩터 백테스트 요청"""

    symbols: List[str] = Field(
        default_factory=list,
        description="유니버스 종목코드. 비우면 DB의 전체 종목 (현재 남아 있는 종목만이라 생존 편향 주의)",
    )
    start_date: str = Field(..., description="성과 측정 시작일 (YYYY-MM-DD)")
    end_date: str = Field(..., description="종료일 (YYYY-MM-DD)")
    lookback: int = Field(default=252, ge=20, le=504, description="모멘텀 계산 기간 (거래일, 252 ≈ 12개월)")
    skip: int = Field(default=21, ge=0, le=63, description="최근 제외 기간 (거래일, 21 ≈ 1개월)")
    rebalance: Literal["M", "W"] = Field(default="M", description="리밸런싱 주기: M 월말, W 주말")
    n_quantiles: int = Field(default=5, ge=2, le=10, description="분위 수")
    initial_capital: float = Field(default=100_000_000, gt=0)
    commission_rate: float = Field(default=0.00015, ge=0, le=0.01)
    tax_rate: float = Field(default=0.002, ge=0, le=0.01)
    slippage: float = Field(default=0.0, ge=0, le=0.01)

    @model_validator(mode="after")
    def _check(self) -> "MomentumFactorRequest":
        if self.skip >= self.lookback:
            raise ValueError("skip은 lookback보다 작아야 합니다.")
        return self


@router.post(
    "/momentum",
    summary="모멘텀 팩터 벡터화 백테스트",
    description="유니버스를 모멘텀(기본 12-1개월) 순위로 분위 포트폴리오를 만들어 성과와 Rank IC를 계산합니다.",
)
def run_momentum(request: MomentumFactorRequest) -> dict:
    started = perf_counter()
    try:
        start = datetime.strptime(request.start_date, "%Y-%m-%d").date()
        end = datetime.strptime(request.end_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="날짜는 YYYY-MM-DD 형식이어야 합니다.")
    if start >= end:
        raise HTTPException(status_code=400, detail="시작일은 종료일보다 앞이어야 합니다.")

    # 첫 리밸런싱부터 팩터를 계산하려면 시작일 이전 lookback 거래일 만큼의 가격이 필요하다.
    history_start = start - timedelta(days=int(request.lookback * _CALENDAR_DAYS_PER_TRADING_DAY) + 10)
    try:
        prices = PostgresDataProvider().get_close_panel(request.symbols, history_start, end)
    except PostgresError as exc:
        logger.exception("[Factor] PostgreSQL 조회 오류")
        raise HTTPException(
            status_code=500,
            detail=f"PostgreSQL 조회 오류: {type(exc).__name__}. DB 연결과 테이블/컬럼 설정을 확인하세요.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if prices.empty:
        raise HTTPException(status_code=400, detail="PostgreSQL에 요청 기간의 가격 데이터가 없습니다.")
    missing = sorted(set(request.symbols) - set(prices.columns))
    loaded = perf_counter()

    try:
        result = run_momentum_backtest(
            prices,
            start=request.start_date,
            end=request.end_date,
            lookback=request.lookback,
            skip=request.skip,
            rebalance=request.rebalance,
            n_quantiles=request.n_quantiles,
            costs=CostModel(
                commission_rate=request.commission_rate,
                tax_rate=request.tax_rate,
                slippage=request.slippage,
            ),
            initial_capital=request.initial_capital,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    result["params"] = request.model_dump()
    result["missing_symbols"] = missing
    result["timing_ms"] = {
        "load": round((loaded - started) * 1000),
        "compute": round((perf_counter() - loaded) * 1000),
    }
    logger.info(
        f"[Factor] momentum universe={result['universe_size']} rebalances={len(result['rebalance_dates'])} "
        f"load_ms={result['timing_ms']['load']} compute_ms={result['timing_ms']['compute']}"
    )
    return {"success": True, "data": result}
