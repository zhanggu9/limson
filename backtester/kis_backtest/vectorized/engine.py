"""벡터화 백테스트 엔진

LEAN(이벤트 기반)은 봉마다 OnData를 돌며 주문을 시뮬레이션한다. 여기서는 가격 패널과
리밸런싱 목표 비중을 행렬로 받아 포트폴리오 수익률을 한 번에 계산한다. 많은 종목·파라미터를
빠르게 훑어보는 용도이고, 최종 검증은 LEAN 백테스트로 한다.

규칙
- t일 종가로 정한 비중은 t+1일 수익률부터 적용한다 (미래 정보 사용 방지).
- 리밸런싱 사이에는 매수 후 보유: 종목 비중이 가격에 따라 흘러간다(drift).
- 리밸런싱 비용 = 매수 금액 × (수수료+슬리피지) + 매도 금액 × (수수료+거래세+슬리피지).
  흘러간 비중에서 새 목표 비중으로 옮기는 만큼만 거래한다.
- 가격이 없는 날(거래정지·상장폐지)은 수익률 0으로 둔다 → 상장폐지 손실은 반영되지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class CostModel:
    commission_rate: float = 0.00015  # 매수·매도 각각
    tax_rate: float = 0.002  # 매도 시 거래세
    slippage: float = 0.0  # 매수·매도 각각

    @property
    def buy_rate(self) -> float:
        return self.commission_rate + self.slippage

    @property
    def sell_rate(self) -> float:
        return self.commission_rate + self.tax_rate + self.slippage


@dataclass
class PortfolioPath:
    """포트폴리오 일별 수익률과 리밸런싱별 회전율"""

    returns: pd.Series  # 리밸런싱 다음 날부터의 일별 순수익률 (비용 차감)
    turnover: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))  # 리밸런싱일 → 회전율

    def equity(self, initial_capital: float = 1.0) -> pd.Series:
        return initial_capital * (1 + self.returns).cumprod()


def simulate_weights(
    prices: pd.DataFrame,
    target_weights: pd.DataFrame,
    costs: CostModel | None = None,
) -> PortfolioPath:
    """리밸런싱 목표 비중으로 포트폴리오 일별 수익률을 계산한다.

    Args:
        prices: 행 = 거래일(오름차순), 열 = 종목. 수정주가 권장.
        target_weights: 행 = 리밸런싱일(prices.index에 있는 날짜), 열 = 종목.
            행 합계 ≤ 1, 나머지는 현금(수익률 0).
        costs: 거래 비용. None이면 비용 없음.
    """
    costs = costs or CostModel(commission_rate=0.0, tax_rate=0.0, slippage=0.0)
    rb = pd.DatetimeIndex(target_weights.index).sort_values()
    if len(rb) == 0:
        raise ValueError("리밸런싱 날짜가 없습니다.")
    missing = rb.difference(prices.index)
    if len(missing):
        raise ValueError(f"가격 데이터에 없는 리밸런싱 날짜: {list(missing[:3])}")

    weights = target_weights.reindex(index=rb, columns=prices.columns).fillna(0.0)
    returns = prices.pct_change(fill_method=None).fillna(0.0)

    # 날짜 d가 속한 보유 기간 k: rb[k] < d <= rb[k+1] → k = (d보다 앞선 리밸런싱 수) - 1
    period = np.searchsorted(rb.values, prices.index.values, side="left") - 1
    held = period >= 0
    dates = prices.index[held]
    period = period[held]
    r = returns.loc[dates]

    # 기간 내 종목별 누적 성장률 G[d, s] (기간 시작 = 1)
    growth = np.exp(np.log1p(r).groupby(period).cumsum())
    w = weights.to_numpy()[period]
    cash = 1.0 - w.sum(axis=1)
    value = cash + (w * growth.to_numpy()).sum(axis=1)  # 기간 시작 대비 포트폴리오 가치

    value = pd.Series(value, index=dates)
    prev_value = value.groupby(period).shift(1).fillna(1.0)
    daily = value / prev_value - 1

    # 리밸런싱 비용: 직전 기간 말 흘러간 비중 → 새 목표 비중
    period_end = pd.Series(np.arange(len(dates)), index=dates).groupby(period).max()
    turnover = np.zeros(len(rb))
    cost_frac = np.zeros(len(rb))
    drifted = np.zeros(len(prices.columns))  # 첫 리밸런싱은 전액 현금에서 시작
    for k in range(len(rb)):
        target = weights.to_numpy()[k]
        buys = np.clip(target - drifted, 0, None).sum()
        sells = np.clip(drifted - target, 0, None).sum()
        turnover[k] = (buys + sells) / 2
        cost_frac[k] = buys * costs.buy_rate + sells * costs.sell_rate
        if k in period_end.index:
            end_pos = period_end[k]
            end_value = value.iloc[end_pos]
            drifted = target * growth.iloc[end_pos].to_numpy() / end_value
        else:
            drifted = target

    first_day = pd.Series(np.arange(len(dates)), index=dates).groupby(period).min()
    first_pos = first_day.to_numpy()
    charged = cost_frac[first_day.index.to_numpy()]
    daily_values = daily.to_numpy().copy()
    daily_values[first_pos] = (1 + daily_values[first_pos]) * (1 - charged) - 1

    return PortfolioPath(
        returns=pd.Series(daily_values, index=dates, name="return"),
        turnover=pd.Series(turnover, index=rb, name="turnover"),
    )


def performance_metrics(path: PortfolioPath) -> Dict[str, float]:
    """연환산 수익률·변동성·샤프·MDD·회전율 (무위험 수익률 0 가정)."""
    r = path.returns
    if r.empty:
        raise ValueError("수익률 데이터가 없습니다.")
    equity = (1 + r).cumprod()
    total = float(equity.iloc[-1] - 1)
    years = len(r) / TRADING_DAYS
    cagr = float(equity.iloc[-1] ** (1 / years) - 1) if years > 0 and equity.iloc[-1] > 0 else float("nan")
    vol = float(r.std(ddof=0) * np.sqrt(TRADING_DAYS))
    sharpe = float(r.mean() / r.std(ddof=0) * np.sqrt(TRADING_DAYS)) if r.std(ddof=0) > 0 else 0.0
    drawdown = equity / equity.cummax() - 1
    return {
        "total_return": total * 100,
        "cagr": cagr * 100,
        "annual_volatility": vol * 100,
        "sharpe_ratio": sharpe,
        "max_drawdown": float(drawdown.min()) * 100,
        "avg_turnover": float(path.turnover.mean()) * 100 if len(path.turnover) else 0.0,
        "rebalance_count": int(len(path.turnover)),
        "trading_days": int(len(r)),
    }
