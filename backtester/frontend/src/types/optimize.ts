/**
 * 하이퍼옵트 타입 — backtester/backend/routes/optimize.py, kis_backtest/optimize/* 와 짝을 이룬다.
 */

import type { BacktestResolution, BarMinutes } from "./backtest";

export type HyperoptObjective = "sharpe" | "sortino" | "calmar" | "return_dd" | "return";

/** 탐색할 수 있는 숫자 하나 */
export interface HyperoptParamSpec {
  name: string;
  label: string;
  type: "int" | "float";
  default: number;
  low: number;
  high: number;
  step: number;
  group: "param" | "indicator" | "condition" | "risk";
  min_allowed: number;
  max_allowed: number;
  suggested: boolean; // 처음에 탐색 대상으로 체크해 둘지
}

/** smaller < larger 를 지켜야 하는 파라미터 쌍 */
export interface HyperoptConstraint {
  smaller: string;
  larger: string;
  label: string;
}

export interface HyperoptObjectiveInfo {
  id: HyperoptObjective;
  label: string;
  description: string;
}

/** POST /api/optimize/space */
export interface HyperoptSpace {
  kind: "preset" | "yaml";
  id: string;
  name: string;
  params: HyperoptParamSpec[];
  constraints: HyperoptConstraint[];
  objectives: HyperoptObjectiveInfo[];
  max_search_params: number;
  max_concurrency: number;
}

export interface HyperoptParamChoice {
  name: string;
  optimize: boolean;
  low?: number;
  high?: number;
  step?: number;
  value?: number;
}

export interface HyperoptRequest {
  strategy_id?: string;
  yaml_content?: string;
  symbols: string[];
  start_date: string;
  end_date: string;
  initial_capital: number;
  commission_rate: number;
  tax_rate: number;
  slippage: number;
  resolution: BacktestResolution;
  bar_minutes?: BarMinutes;
  intraday_exit?: string | null;
  params: HyperoptParamChoice[];
  objective: HyperoptObjective;
  n_trials: number;
  min_trades: number;
  validation_ratio: number;
  concurrency: number;
  time_budget_minutes: number;
  seed?: number | null;
}

export interface HyperoptMetrics {
  total_return: number;
  cagr: number;
  max_drawdown: number;
  sharpe: number;
  sortino: number;
  win_rate: number;
  profit_loss_ratio: number;
  trades: number; // 매수 체결 수
  orders: number;
  final_capital: number;
}

export type HyperoptTrialState = "ok" | "few_trades" | "no_score" | "failed";

export interface HyperoptTrial {
  number: number; // 기본값 시행 0, 탐색 시행 1부터
  kind: "baseline" | "trial";
  params: Record<string, number>;
  state: HyperoptTrialState;
  score: number | null;
  metrics: HyperoptMetrics | null;
  error: string | null;
  duration: number;
  attempts: number;
}

export interface HyperoptBest {
  number: number;
  score: number | null;
  params: Record<string, number>;
}

export interface HyperoptPeriod {
  start: string;
  end: string;
}

export interface HyperoptStartEvent {
  type: "start";
  opt_id: string;
  strategy: { kind: "preset" | "yaml"; id: string; name: string };
  objective: HyperoptObjectiveInfo;
  n_trials: number;
  min_trades: number;
  concurrency: number;
  resolution: BacktestResolution;
  bar_minutes: BarMinutes | null;
  train: HyperoptPeriod;
  valid: HyperoptPeriod | null;
  space: HyperoptParamSpec[];
  fixed: Record<string, number>;
  constraints: HyperoptConstraint[];
  estimate: { per_run: number; total: number; timeout: number };
  time_budget: number;
  warnings: string[];
}

export interface HyperoptPeriodResult {
  state: string;
  score: number | null;
  metrics: HyperoptMetrics | null;
  equity_curve: Record<string, number> | null;
  error?: string | null;
}

export interface HyperoptCandidate {
  rank: number | null; // 기본값만 있는 줄은 null
  label: string;
  is_default: boolean;
  number: number;
  params: Record<string, number>;
  full_params: Record<string, number>;
  train: HyperoptPeriodResult;
  valid: HyperoptPeriodResult | null;
  yaml: string | null; // YAML 전략이면 이 값을 넣은 YAML
}

export type HyperoptStopReason = "completed" | "time_budget" | "space_exhausted" | "cancelled";

export interface HyperoptDoneEvent {
  type: "done";
  stopped_reason: HyperoptStopReason;
  elapsed: number;
  counts: {
    trials: number;
    ok: number;
    few_trades: number;
    no_score: number;
    failed: number;
    duplicates: number;
    rejected: number;
    retries: number;
  };
  baseline: HyperoptTrial | null;
  candidates: HyperoptCandidate[];
  importance: Record<string, number> | null;
}

export type HyperoptEvent =
  | HyperoptStartEvent
  | { type: "phase"; phase: "data" | "search" | "validate"; message?: string; total?: number; elapsed?: number }
  | (HyperoptTrial & { type: "trial"; best: HyperoptBest | null; done: number; total: number; elapsed: number; eta: number })
  | { type: "retry"; kind: string; number: number; attempt: number; message: string }
  | {
      type: "validation";
      index: number;
      label: string;
      valid: Omit<HyperoptPeriodResult, "equity_curve">;
      done: number;
      total: number;
      elapsed: number;
    }
  | { type: "warnings"; warnings: string[] }
  | { type: "ping"; elapsed?: number; running?: number }
  | HyperoptDoneEvent
  | { type: "error"; message: string }
  | { type: "cancelled"; elapsed: number };
