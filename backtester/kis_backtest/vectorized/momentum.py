"""횡단면 모멘텀 팩터 백테스트

리밸런싱일마다 유니버스 종목을 모멘텀 순위로 n개 분위로 나눠 각 분위를 동일가중 보유한다.
- 전략: 최상위 분위(모멘텀이 가장 강한 종목) 롱온리
- 비교: 유니버스 동일가중, 분위별 수익률, 최상위-최하위 롱숏 스프레드
- 팩터 유효성: 리밸런싱별 Rank IC (팩터 순위 vs 다음 기간 수익률 순위)
"""

from __future__ import annotations

from typing import Any, Dict, Literal

import numpy as np
import pandas as pd

from .engine import CostModel, PortfolioPath, performance_metrics, simulate_weights
from .factors import momentum_factor, quantile_buckets, rank_ic, rebalance_dates


def _equal_weights(mask: pd.DataFrame) -> pd.DataFrame:
    counts = mask.sum(axis=1)
    return mask.astype(float).div(counts.replace(0, np.nan), axis=0).fillna(0.0)


def _curve(path: PortfolioPath, initial_capital: float) -> Dict[str, float]:
    equity = path.equity(initial_capital)
    return {d.strftime("%Y-%m-%d"): round(float(v), 2) for d, v in equity.items()}


def run_momentum_backtest(
    prices: pd.DataFrame,
    start: str,
    end: str,
    lookback: int = 252,
    skip: int = 21,
    rebalance: Literal["M", "W"] = "M",
    n_quantiles: int = 5,
    costs: CostModel | None = None,
    initial_capital: float = 100_000_000,
    top_n_latest: int = 20,
) -> Dict[str, Any]:
    """모멘텀 팩터 백테스트.

    Args:
        prices: 행 = 거래일, 열 = 종목. 시작일 이전 lookback 거래일 이상의 기록이 있어야
            첫 리밸런싱부터 팩터가 계산된다.
        start, end: 성과를 측정할 기간 (YYYY-MM-DD)
    """
    prices = prices.sort_index()
    prices = prices.loc[: pd.Timestamp(end)].dropna(how="all", axis=1)
    if prices.empty:
        raise ValueError("요청 기간의 가격 데이터가 없습니다.")

    factor = momentum_factor(prices, lookback=lookback, skip=skip)
    rb = rebalance_dates(prices.index, pd.Timestamp(start), rebalance)
    if len(rb) == 0:
        raise ValueError("리밸런싱할 날짜가 없습니다. 기간을 늘리세요.")

    factor_rb = factor.loc[rb]
    buckets = quantile_buckets(factor_rb, n_quantiles)
    valid_rb = buckets.notna().any(axis=1)
    if not valid_rb.any():
        raise ValueError(
            f"모멘텀을 계산할 수 있는 종목이 {n_quantiles}개 미만입니다. "
            f"종목 수를 늘리거나 시작일 이전 {lookback}거래일 이상의 가격 데이터가 있는지 확인하세요."
        )
    # 팩터를 계산할 수 없는 초기 리밸런싱일은 현금으로 둔다 (행은 남겨 기간 구분을 유지).
    available = prices.loc[rb].notna()

    quantile_paths = {
        q: simulate_weights(prices, _equal_weights(buckets == q), costs)
        for q in range(1, n_quantiles + 1)
    }
    top = quantile_paths[n_quantiles]
    bottom = quantile_paths[1]
    benchmark = simulate_weights(prices, _equal_weights(available.where(valid_rb, False, axis=0)), costs)
    spread = PortfolioPath(
        returns=(top.returns - bottom.returns).rename("return"),
        turnover=(top.turnover + bottom.turnover),
    )

    # Rank IC: 리밸런싱 k의 팩터 vs rb[k] → rb[k+1] 수익률 (마지막 구간은 데이터 끝까지)
    rb_prices = prices.loc[rb]
    next_prices = prices.reindex(list(rb[1:]) + [prices.index[-1]])
    next_prices.index = rb
    forward = next_prices / rb_prices - 1
    ic = rank_ic(factor_rb.where(valid_rb, axis=0), forward).dropna()

    latest_date = prices.index[-1]
    latest = factor.loc[latest_date].dropna().sort_values(ascending=False)
    latest_q = quantile_buckets(latest.to_frame().T, n_quantiles).iloc[0] if len(latest) >= n_quantiles else None

    def _rows(series: pd.Series) -> list:
        return [
            {
                "symbol": sym,
                "momentum": round(float(val) * 100, 2),
                "quantile": int(latest_q[sym]) if latest_q is not None else None,
            }
            for sym, val in series.items()
        ]

    ic_std = float(ic.std(ddof=1)) if len(ic) > 1 else float("nan")
    ic_mean = float(ic.mean()) if len(ic) else float("nan")
    ic_ir = ic_mean / ic_std if ic_std and np.isfinite(ic_std) and ic_std > 0 else float("nan")

    def _clean(x: float) -> float | None:
        return None if x is None or not np.isfinite(x) else round(float(x), 4)

    return {
        "period": {
            "start": top.returns.index[0].strftime("%Y-%m-%d"),
            "end": top.returns.index[-1].strftime("%Y-%m-%d"),
        },
        "universe_size": int(prices.shape[1]),
        "rebalance_dates": [d.strftime("%Y-%m-%d") for d in rb],
        "strategy": {
            "label": f"모멘텀 상위 분위 (Q{n_quantiles}) 롱온리",
            "metrics": performance_metrics(top),
            "equity_curve": _curve(top, initial_capital),
        },
        "benchmark": {
            "label": "유니버스 동일가중",
            "metrics": performance_metrics(benchmark),
            "equity_curve": _curve(benchmark, initial_capital),
        },
        "quantiles": [
            {
                "quantile": q,
                "metrics": performance_metrics(path),
                "equity_curve": _curve(path, initial_capital),
            }
            for q, path in quantile_paths.items()
        ],
        "long_short": {
            "label": f"Q{n_quantiles} - Q1 롱숏 (공매도 비용 미반영)",
            "metrics": performance_metrics(spread),
            "equity_curve": _curve(spread, initial_capital),
        },
        "ic": {
            "mean": _clean(ic_mean),
            "std": _clean(ic_std),
            "ir": _clean(ic_ir),
            "t_stat": _clean(ic_ir * np.sqrt(len(ic))) if np.isfinite(ic_ir) else None,
            "positive_ratio": _clean(float((ic > 0).mean())) if len(ic) else None,
            "count": int(len(ic)),
            "series": {d.strftime("%Y-%m-%d"): round(float(v), 4) for d, v in ic.items()},
        },
        "latest": {
            "date": latest_date.strftime("%Y-%m-%d"),
            "top": _rows(latest.head(top_n_latest)),
            "bottom": _rows(latest.tail(top_n_latest).iloc[::-1]),
        },
    }
