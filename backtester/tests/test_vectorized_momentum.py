"""벡터화 백테스트 엔진·모멘텀 팩터 테스트.

엔진 결과를 하루씩 도는 단순 루프 구현과 비교해 같은 값이 나오는지 확인한다.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

BACKTESTER_DIR = Path(__file__).resolve().parents[1]
if str(BACKTESTER_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTESTER_DIR))

from kis_backtest.vectorized import (  # noqa: E402
    CostModel,
    momentum_factor,
    performance_metrics,
    quantile_buckets,
    rank_ic,
    rebalance_dates,
    run_momentum_backtest,
    simulate_weights,
)


def _prices(n_days=600, n_symbols=12, seed=1, drift=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2022-01-03", periods=n_days)
    mu = np.zeros(n_symbols) if drift is None else drift
    rets = rng.normal(mu, 0.015, size=(n_days, n_symbols))
    return pd.DataFrame(
        100 * np.exp(np.cumsum(rets, axis=0)),
        index=idx,
        columns=[f"{i:06d}" for i in range(n_symbols)],
    )


def _loop_reference(prices, target_weights, costs):
    """같은 규칙을 직관적으로: 비용은 리밸런싱 다음 날 수익률에서 차감."""
    rb = set(target_weights.index)
    first_rb = min(rb)
    holdings = pd.Series(0.0, index=prices.columns)
    cash = 1.0
    out = {}
    pending_fee_frac = 0.0
    for i, d in enumerate(prices.index):
        if i > 0 and d > first_rb:
            start_value = holdings.sum() + cash
            r = (prices.iloc[i] / prices.iloc[i - 1] - 1).fillna(0.0)
            holdings = holdings * (1 + r)
            end_value = holdings.sum() + cash
            gross = end_value / start_value - 1
            out[d] = (1 + gross) * (1 - pending_fee_frac) - 1
            if pending_fee_frac:
                holdings *= 1 - pending_fee_frac
                cash *= 1 - pending_fee_frac
            pending_fee_frac = 0.0
        if d in rb and d != prices.index[-1]:
            value = holdings.sum() + cash
            current = holdings / value
            target = target_weights.loc[d].reindex(prices.columns).fillna(0.0)
            buys = (target - current).clip(lower=0).sum()
            sells = (current - target).clip(lower=0).sum()
            pending_fee_frac = buys * costs.buy_rate + sells * costs.sell_rate
            holdings = target * value
            cash = value * (1 - target.sum())
    return pd.Series(out)


@pytest.mark.parametrize("costs", [CostModel(0, 0, 0), CostModel(0.00015, 0.002, 0.001)])
def test_engine_matches_loop_reference(costs):
    prices = _prices()
    prices.iloc[:40, 3] = np.nan  # 늦게 상장한 종목
    prices.iloc[300:320, 5] = np.nan  # 거래정지
    rb = rebalance_dates(prices.index, prices.index[60], "M")
    rng = np.random.default_rng(7)
    weights = pd.DataFrame(rng.random((len(rb), prices.shape[1])), index=rb, columns=prices.columns)
    weights[prices.loc[rb].isna()] = 0.0
    weights = weights.div(weights.sum(axis=1), axis=0) * 0.9  # 10% 현금

    vec = simulate_weights(prices, weights, costs).returns
    ref = _loop_reference(prices, weights, costs).loc[vec.index]
    np.testing.assert_allclose(vec.to_numpy(), ref.to_numpy(), atol=1e-12)


def test_weights_apply_from_next_day():
    """리밸런싱일 당일 수익률은 새 비중에 반영되지 않는다 (미래 정보 사용 방지)."""
    idx = pd.bdate_range("2024-01-01", periods=5)
    prices = pd.DataFrame({"A": [100, 200, 200, 200, 210]}, index=idx, dtype=float)
    weights = pd.DataFrame({"A": [1.0]}, index=[idx[1]])
    path = simulate_weights(prices, weights)
    assert list(path.returns.index) == list(idx[2:])
    np.testing.assert_allclose(path.returns.to_numpy(), [0.0, 0.0, 0.05])


def test_momentum_factor_skips_recent_month():
    idx = pd.bdate_range("2024-01-01", periods=30)
    prices = pd.DataFrame({"A": np.arange(1, 31, dtype=float)}, index=idx)
    mom = momentum_factor(prices, lookback=10, skip=2)
    assert np.isnan(mom["A"].iloc[9])
    assert mom["A"].iloc[20] == pytest.approx(prices["A"].iloc[18] / prices["A"].iloc[10] - 1)
    with pytest.raises(ValueError):
        momentum_factor(prices, lookback=5, skip=5)


def test_quantiles_and_rank_ic():
    factor = pd.DataFrame([[1, 2, 3, 4, 5, np.nan], [5, 4, 3, 2, 1, 0]], columns=list("ABCDEF"), dtype=float)
    buckets = quantile_buckets(factor, 5)
    assert buckets.iloc[0].tolist()[:5] == [1, 2, 3, 4, 5] and np.isnan(buckets.iloc[0, 5])
    ic = rank_ic(factor, factor * 2)
    np.testing.assert_allclose(ic.to_numpy(), [1.0, 1.0])
    np.testing.assert_allclose(rank_ic(factor, -factor).to_numpy(), [-1.0, -1.0])


def test_momentum_ranks_trending_stocks_on_top():
    """수익률 추세가 확실한 종목일수록 상위 분위에 들어가고 IC가 양수여야 한다."""
    drift = np.linspace(-0.002, 0.002, 20)
    prices = _prices(n_days=700, n_symbols=20, seed=3, drift=drift)
    result = run_momentum_backtest(
        prices, start=prices.index[300].strftime("%Y-%m-%d"), end=prices.index[-1].strftime("%Y-%m-%d"),
        lookback=120, skip=5, n_quantiles=4, costs=CostModel(),
    )
    cagr = [q["metrics"]["cagr"] for q in result["quantiles"]]
    assert cagr[-1] == max(cagr) and cagr[0] < 0 < cagr[-1]  # 최상위 분위가 가장 좋고 최하위는 손실
    assert cagr[-1] - cagr[0] > 50
    assert result["strategy"]["metrics"]["cagr"] > result["benchmark"]["metrics"]["cagr"]
    assert result["ic"]["mean"] > 0.15 and result["ic"]["t_stat"] > 3
    assert {row["symbol"] for row in result["latest"]["top"][:5]} <= {f"{i:06d}" for i in range(10, 20)}
    assert result["period"]["start"] > prices.index[300].strftime("%Y-%m-%d")


def test_momentum_requires_enough_history():
    prices = _prices(n_days=100, n_symbols=6)
    with pytest.raises(ValueError, match="모멘텀을 계산할 수 있는 종목"):
        run_momentum_backtest(prices, start=prices.index[10].strftime("%Y-%m-%d"),
                              end=prices.index[-1].strftime("%Y-%m-%d"), lookback=252, skip=21)


def test_performance_metrics_known_values():
    from kis_backtest.vectorized import PortfolioPath

    r = pd.Series([0.1, -0.5, 0.2], index=pd.bdate_range("2024-01-01", periods=3))
    m = performance_metrics(PortfolioPath(returns=r))
    assert m["total_return"] == pytest.approx((1.1 * 0.5 * 1.2 - 1) * 100)
    assert m["max_drawdown"] == pytest.approx(-50.0)


def test_factor_api_end_to_end(monkeypatch):
    """/api/factor/momentum: DB 조회를 가짜로 바꿔 요청 → 계산 → 응답 형식까지 확인."""
    from fastapi.testclient import TestClient

    from backend.main import app
    from kis_backtest.providers.postgres import PostgresDataProvider

    prices = _prices(n_days=700, n_symbols=12, seed=5, drift=np.linspace(-0.001, 0.001, 12))
    seen = {}

    def fake_panel(self, symbols, start, end):
        seen["args"] = (symbols, start, end)
        return prices

    monkeypatch.setenv("DATABASE_URL", "postgresql://unused@127.0.0.1:1/unused")
    monkeypatch.setattr(PostgresDataProvider, "get_close_panel", fake_panel)
    client = TestClient(app)
    body = {"start_date": prices.index[400].strftime("%Y-%m-%d"),
            "end_date": prices.index[-1].strftime("%Y-%m-%d"),
            "lookback": 126, "skip": 21, "n_quantiles": 3, "symbols": ["000001", "999999"]}
    response = client.post("/api/factor/momentum", json=body)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data["quantiles"]) == 3 and data["universe_size"] == 12
    assert data["missing_symbols"] == ["999999"]
    assert seen["args"][1] < prices.index[400].date()  # lookback 만큼 앞에서부터 조회

    bad = client.post("/api/factor/momentum", json={**body, "skip": 200})
    assert bad.status_code == 422
