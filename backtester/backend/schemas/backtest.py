"""Backtest API Schemas.

"""

from datetime import date
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field

# 분봉 주기 (kis_backtest.codegen.generator.BAR_MINUTES_CHOICES 와 같은 값)
BarMinutes = Literal[1, 3, 5, 10, 15, 30, 60]


class BacktestRequest(BaseModel):
    """백테스트 요청"""
    strategy_id: str = Field(..., description="전략 ID")
    symbols: List[str] = Field(..., description="종목 코드 리스트")
    start_date: Union[str, date] = Field(..., description="시작일 (YYYY-MM-DD)")
    end_date: Union[str, date] = Field(..., description="종료일 (YYYY-MM-DD)")
    initial_capital: float = Field(default=100_000_000, description="초기 자본")
    param_overrides: Optional[Dict[str, Any]] = Field(
        default=None,
        description="파라미터 오버라이드 (예: {'period': 21, 'oversold': 25})"
    )
    commission_rate: Optional[float] = Field(
        default=0.00015,
        description="수수료율 (기본 0.00015 = 0.015%)"
    )
    tax_rate: Optional[float] = Field(
        default=0.002,
        description="거래세율 (기본 0.002 = 0.2%, 매도 시)"
    )
    slippage: Optional[float] = Field(
        default=0.0,
        description="슬리피지 (기본 0 = 0%)"
    )
    resolution: Literal["daily", "minute"] = Field(
        default="daily",
        description="봉 단위: daily(일봉) | minute(분봉) — 둘 다 PostgreSQL"
    )
    bar_minutes: BarMinutes = Field(
        default=1,
        description="분봉 주기(분). resolution=minute 일 때만 1이 아닌 값을 쓸 수 있다"
    )
    intraday_exit: Optional[str] = Field(
        default=None,
        pattern=r"^\d{2}:\d{2}$",
        description="장마감 청산 시각 HH:MM (분봉 전용). 이후 신규 진입 금지 + 보유분 청산"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "strategy_id": "rsi_oversold",
                "symbols": ["005930", "000660"],
                "start_date": "2024-01-01",
                "end_date": "2024-12-31",
                "initial_capital": 100000000,
                "param_overrides": {"period": 21, "oversold": 25},
                "commission_rate": 0.00015,
                "tax_rate": 0.002,
                "slippage": 0.001,
            }
        }


class BacktestResponse(BaseModel):
    """백테스트 응답"""
    success: bool = True
    data: Dict[str, Any]
    message: Optional[str] = None
