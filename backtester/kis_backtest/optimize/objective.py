"""하이퍼옵트 목표 점수 (클수록 좋다)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional

# 최대 낙폭이 아주 작을 때(거래 1~2번) 비율 점수가 폭주하지 않도록 낙폭을 최소 1%로 본다.
_MIN_DRAWDOWN = 1.0


@dataclass(frozen=True)
class Objective:
    id: str
    label: str
    description: str
    score: Callable[[Mapping[str, float]], float]

    def to_dict(self) -> Dict[str, str]:
        return {"id": self.id, "label": self.label, "description": self.description}


OBJECTIVES: Dict[str, Objective] = {
    objective.id: objective
    for objective in (
        Objective("sharpe", "샤프 비율", "변동성 대비 수익. 가장 많이 쓰는 기준", lambda m: m["sharpe"]),
        Objective("sortino", "소르티노 비율", "떨어질 때의 변동성만 위험으로 보는 샤프", lambda m: m["sortino"]),
        Objective(
            "calmar", "칼마 비율", "연 수익률 ÷ 최대 낙폭",
            lambda m: m["cagr"] / max(m["max_drawdown"], _MIN_DRAWDOWN),
        ),
        Objective(
            "return_dd", "수익률 ÷ 최대 낙폭", "총 수익률 ÷ 최대 낙폭",
            lambda m: m["total_return"] / max(m["max_drawdown"], _MIN_DRAWDOWN),
        ),
        Objective("return", "총 수익률", "위험은 보지 않고 수익률만", lambda m: m["total_return"]),
    )
}


def summarize_result(result: Mapping[str, Any]) -> Dict[str, float]:
    """백테스트 API 응답(_lean_run_to_api_response)에서 점수 계산에 쓰는 지표만 뽑는다."""
    metrics = result.get("metrics") or {}
    basic = metrics.get("basic") or {}
    risk = metrics.get("risk") or {}
    trading = metrics.get("trading") or {}
    trades = result.get("trades") or []
    return {
        "total_return": float(basic.get("total_return") or 0.0),
        "cagr": float(basic.get("annual_return") or 0.0),
        "max_drawdown": abs(float(basic.get("max_drawdown") or 0.0)),
        "sharpe": float(risk.get("sharpe_ratio") or 0.0),
        "sortino": float(risk.get("sortino_ratio") or 0.0),
        "win_rate": float(trading.get("win_rate") or 0.0),
        "profit_loss_ratio": float(trading.get("profit_loss_ratio") or 0.0),
        "trades": float(sum(1 for trade in trades if trade.get("direction") == "Buy")),
        "orders": float(len(trades)),
        "final_capital": float(result.get("final_capital") or 0.0),
    }


def score_metrics(objective_id: str, metrics: Mapping[str, float]) -> Optional[float]:
    """목표 점수. 계산할 수 없으면(NaN 등) None."""
    try:
        value = float(OBJECTIVES[objective_id].score(metrics))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return value if math.isfinite(value) else None
