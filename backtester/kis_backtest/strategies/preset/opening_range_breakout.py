"""Opening Range Breakout (ORB) 전략.

Single Source of Truth:
- 시가 범위: 장 시작 후 N분 동안의 고가·저가 (그날 첫 봉 시작 시각 기준)
- 진입: 범위가 완성된 뒤 종가가 범위 고가를 넘으면 매수
- 청산: 종가가 범위 저가 아래로 내려가면 매도 (+ 손절·익절 %)

⚠️ 분봉 전용 당일 매매 전략. 장마감 청산(intraday_exit)과 함께 쓴다.
"""

from __future__ import annotations

from typing import Any, Dict

from kis_backtest.strategies.base import BaseStrategy
from kis_backtest.strategies.registry import register
from kis_backtest.core.strategy import StrategyDefinition
from kis_backtest.core.condition import Condition
from kis_backtest.core.indicator import Indicator
from kis_backtest.core.risk import RiskManagement
from kis_backtest.dsl.helpers import Price


@register(
    "opening_range_breakout",
    name="시가 범위 돌파 (ORB)",
    category="momentum",
    description="장 시작 후 N분 고가를 돌파하면 매수, 저가를 이탈하면 매도 (분봉 전용)",
    tags=["orb", "breakout", "intraday", "minute"],
)
class OpeningRangeBreakoutStrategy(BaseStrategy):
    """Opening Range Breakout 전략

    Parameters:
        range_minutes: 시가 범위 길이(분) (default: 30)
        stop_loss_pct: 손절 비율 % (default: 2.0)
        take_profit_pct: 익절 비율 % (default: 4.0)

    Entry Condition (매수):
        종가 > 시가 범위 고가 (범위 완성 후)

    Exit Condition (매도):
        종가 < 시가 범위 저가
        OR 손절 / 익절
    """

    # PARAM_DEFINITIONS - Single Source of Truth
    PARAM_DEFINITIONS = {
        "range_minutes": {"default": 30, "min": 5, "max": 120, "type": "int", "description": "시가 범위 (분)"},
        "stop_loss_pct": {"default": 2.0, "min": 0.5, "max": 10, "type": "float", "description": "손절 %"},
        "take_profit_pct": {"default": 4.0, "min": 1, "max": 20, "type": "float", "description": "익절 %"},
    }

    range_minutes: int = 30
    stop_loss_pct: float = 2.0
    take_profit_pct: float = 4.0

    def __init__(
        self,
        range_minutes: int = 30,
        stop_loss_pct: float = 2.0,
        take_profit_pct: float = 4.0,
    ):
        self.range_minutes = range_minutes
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct

    @property
    def id(self) -> str:
        return "opening_range_breakout"

    @property
    def name(self) -> str:
        return "시가 범위 돌파 (ORB)"

    @property
    def category(self) -> str:
        return "momentum"

    @property
    def description(self) -> str:
        return f"장 시작 후 {self.range_minutes}분 고가 돌파 시 매수, 저가 이탈 시 매도 (분봉 전용)"

    def _opening_range(self, output: str) -> Indicator:
        return Indicator("opening_range", {"minutes": self.range_minutes}, alias="orb", output=output)

    def indicators(self) -> list:
        """사용하는 지표 목록"""
        return [Indicator("opening_range", {"minutes": self.range_minutes}, alias="orb")]

    def entry_condition(self) -> Condition:
        """진입 조건: 종가 > 시가 범위 고가"""
        return Price.close() > self._opening_range("high")

    def exit_condition(self) -> Condition:
        """청산 조건: 종가 < 시가 범위 저가"""
        return Price.close() < self._opening_range("low")

    def risk_management(self) -> RiskManagement:
        """리스크 관리"""
        return RiskManagement(
            stop_loss_pct=self.stop_loss_pct,
            take_profit_pct=self.take_profit_pct,
        )

    def build(self) -> StrategyDefinition:
        """StrategyDefinition 빌드"""
        return StrategyDefinition(
            id=self.id,
            name=self.name,
            category=self.category,
            description=self.description,
            indicators=[ind.to_dict() for ind in self.indicators()],
            entry=self.entry_condition().to_dict(),
            exit=self.exit_condition().to_dict(),
            risk_management=self.risk_management().to_dict(),
            params=self._build_params(),  # PARAM_DEFINITIONS 기반
            metadata={
                "resolution": "minute",  # 분봉 전용
                "intraday": True,  # 장마감 청산과 함께 쓴다
            },
        )

    def to_lean_params(self) -> Dict[str, Any]:
        """Lean 코드 생성용 파라미터"""
        return {
            "orb": {
                "lean_class": "",
                "init": f"opening_range({self.range_minutes})",
                "value": "orb['high'], orb['low']",
                "warmup": 1,
            },
            "entry": {
                "type": "greater_than",
                "lean_condition": "price > orb_high",
            },
            "exit": {
                "type": "less_than",
                "lean_condition": "price < orb_low",
            },
        }
