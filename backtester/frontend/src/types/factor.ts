/**
 * 모멘텀 팩터 (벡터화 백테스트) 타입
 */

export type RebalanceFrequency = "M" | "W";

export interface MomentumFactorRequest {
  symbols: string[]; // 비우면 DB 전체 종목
  start_date: string;
  end_date: string;
  lookback: number;
  skip: number;
  rebalance: RebalanceFrequency;
  n_quantiles: number;
  initial_capital: number;
  commission_rate: number;
  tax_rate: number;
  slippage: number;
}

export interface FactorMetrics {
  total_return: number;
  cagr: number;
  annual_volatility: number;
  sharpe_ratio: number;
  max_drawdown: number;
  avg_turnover: number;
  rebalance_count: number;
  trading_days: number;
}

export interface FactorPortfolio {
  label?: string;
  metrics: FactorMetrics;
  equity_curve: Record<string, number>;
}

export interface FactorRankingRow {
  symbol: string;
  momentum: number; // %
  quantile: number | null;
}

export interface MomentumFactorResult {
  period: { start: string; end: string };
  universe_size: number;
  rebalance_dates: string[];
  strategy: FactorPortfolio;
  benchmark: FactorPortfolio;
  quantiles: (FactorPortfolio & { quantile: number })[];
  long_short: FactorPortfolio;
  ic: {
    mean: number | null;
    std: number | null;
    ir: number | null;
    t_stat: number | null;
    positive_ratio: number | null;
    count: number;
    series: Record<string, number>;
  };
  latest: { date: string; top: FactorRankingRow[]; bottom: FactorRankingRow[] };
  missing_symbols: string[];
  timing_ms: { load: number; compute: number };
}

export interface MomentumFactorResponse {
  success: boolean;
  data: MomentumFactorResult;
}
