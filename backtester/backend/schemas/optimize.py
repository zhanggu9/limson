"""하이퍼옵트 API 스키마."""

from typing import List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from backend.schemas.backtest import BarMinutes

# 종목 코드는 파일 경로에도 쓰이므로 영숫자만 받는다.
_SYMBOL_CHARS = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")

ObjectiveId = Literal["sharpe", "sortino", "calmar", "return_dd", "return"]


class ParamChoice(BaseModel):
    """파라미터 하나의 탐색 설정."""

    name: str = Field(..., min_length=1, max_length=200)
    optimize: bool = Field(default=True, description="true면 범위 안에서 찾고, false면 value로 고정")
    low: Optional[float] = None
    high: Optional[float] = None
    step: Optional[float] = Field(default=None, gt=0)
    value: Optional[float] = Field(default=None, description="optimize=false일 때 고정값 (없으면 기본값)")


class HyperoptSpaceRequest(BaseModel):
    """탐색 공간 조회: 프리셋 strategy_id 또는 YAML 중 하나."""

    strategy_id: Optional[str] = Field(default=None, max_length=100)
    yaml_content: Optional[str] = Field(default=None, max_length=200_000)


class HyperoptRequest(HyperoptSpaceRequest):
    """하이퍼옵트 실행 요청."""

    symbols: List[str] = Field(..., min_length=1, max_length=20)
    start_date: str
    end_date: str
    initial_capital: float = Field(default=100_000_000, gt=0)
    commission_rate: Optional[float] = Field(default=0.00015, ge=0, le=0.05)
    tax_rate: Optional[float] = Field(default=0.002, ge=0, le=0.05)
    slippage: Optional[float] = Field(default=0.0, ge=0, le=0.05)
    resolution: Literal["daily", "minute"] = "daily"
    bar_minutes: BarMinutes = 1
    intraday_exit: Optional[str] = Field(default=None, pattern=r"^\d{2}:\d{2}$")

    params: List[ParamChoice] = Field(default_factory=list, max_length=60)
    objective: ObjectiveId = "sharpe"
    n_trials: int = Field(default=100, ge=5, le=500)
    min_trades: int = Field(default=3, ge=0, le=100_000)
    validation_ratio: float = Field(default=0.3, ge=0, le=0.5, description="뒤쪽 기간 중 검증에 쓸 비율 (0이면 검증 안 함)")
    concurrency: int = Field(default=4, ge=1, le=8)
    time_budget_minutes: int = Field(default=60, ge=1, le=240)
    seed: Optional[int] = Field(default=None, ge=0, le=2**31 - 1)

    @field_validator("symbols")
    @classmethod
    def validate_symbols(cls, symbols: List[str]) -> List[str]:
        cleaned: List[str] = []
        for symbol in symbols:
            code = symbol.strip()
            if not code or len(code) > 12 or any(char not in _SYMBOL_CHARS for char in code):
                raise ValueError(f"종목 코드 형식이 아닙니다: {symbol!r}")
            if code not in cleaned:
                cleaned.append(code)
        return cleaned
