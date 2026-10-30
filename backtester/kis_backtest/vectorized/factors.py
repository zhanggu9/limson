"""횡단면 팩터 계산 (벡터화)

가격 패널(행: 거래일, 열: 종목)을 받아 날짜×종목 팩터 값을 한 번에 계산한다.
"""

from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd


def momentum_factor(prices: pd.DataFrame, lookback: int = 252, skip: int = 21) -> pd.DataFrame:
    """가격 모멘텀 (기본 12-1 모멘텀).

    momentum(t) = P(t - skip) / P(t - lookback) - 1

    최근 skip일(기본 1개월)을 빼는 이유: 단기 반전 효과가 모멘텀 신호를 갉아먹는 것을 피한다.
    lookback일 이전 가격이 없으면 NaN (상장 직후 종목은 자연히 제외된다).
    """
    if lookback <= skip:
        raise ValueError("lookback은 skip보다 커야 합니다.")
    if skip < 0:
        raise ValueError("skip은 0 이상이어야 합니다.")
    return prices.shift(skip) / prices.shift(lookback) - 1


def rebalance_dates(
    index: pd.DatetimeIndex,
    start: pd.Timestamp,
    freq: Literal["M", "W"] = "M",
) -> pd.DatetimeIndex:
    """리밸런싱 날짜: 시작일 직전(또는 당일) 거래일 + 그 뒤 매월/매주 마지막 거래일.

    데이터 마지막 거래일은 이후 수익률이 없으므로 리밸런싱하지 않는다.
    """
    if freq not in ("M", "W"):
        raise ValueError("freq는 'M' 또는 'W'만 지원합니다.")
    index = pd.DatetimeIndex(index).sort_values()
    before_start = index[index <= start]
    if len(before_start) == 0:
        raise ValueError("시작일 이전 가격 데이터가 없습니다.")
    first = before_start[-1]

    later = index[index > first]
    period_ends = pd.Series(later, index=later).groupby(later.to_period(freq)).max()
    dates = pd.DatetimeIndex([first]).append(pd.DatetimeIndex(period_ends.values))
    return dates[dates < index[-1]].unique().sort_values()


def quantile_buckets(factor_rows: pd.DataFrame, n_quantiles: int) -> pd.DataFrame:
    """날짜별 횡단면 분위 (1 = 팩터 최저, n = 최고). 유효 종목이 n개 미만인 날은 전부 NaN."""
    if n_quantiles < 2:
        raise ValueError("n_quantiles는 2 이상이어야 합니다.")
    pct = factor_rows.rank(axis=1, pct=True, method="first")
    buckets = np.ceil(pct * n_quantiles).clip(1, n_quantiles)
    enough = factor_rows.notna().sum(axis=1) >= n_quantiles
    return buckets.where(enough, np.nan, axis=0)


def rank_ic(factor_rows: pd.DataFrame, forward_returns: pd.DataFrame) -> pd.Series:
    """날짜별 Rank IC (팩터 순위와 다음 기간 수익률 순위의 상관계수, Spearman).

    두 값이 모두 있는 종목만 쓰고, 3종목 미만인 날은 NaN.
    """
    mask = factor_rows.notna() & forward_returns.notna()
    a = factor_rows.where(mask).rank(axis=1)
    b = forward_returns.where(mask).rank(axis=1)
    a = a.sub(a.mean(axis=1), axis=0)
    b = b.sub(b.mean(axis=1), axis=0)
    cov = (a * b).sum(axis=1)
    denom = np.sqrt((a ** 2).sum(axis=1) * (b ** 2).sum(axis=1))
    ic = cov / denom.replace(0, np.nan)
    return ic.where(mask.sum(axis=1) >= 3)
