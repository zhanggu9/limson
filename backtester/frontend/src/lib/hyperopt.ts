/**
 * 하이퍼옵트 화면 도우미: 파라미터 범위 입력 검사, 요청 만들기, 예상 시간, 표시용 이름
 */

import { LEAN_BARS_PER_SECOND, LEAN_STARTUP_SECONDS, countWeekdays } from "./backtestOptions";
import type {
  BacktestResolution,
  HyperoptParamChoice,
  HyperoptParamSpec,
  HyperoptSpace,
  HyperoptStopReason,
  HyperoptTrialState,
} from "@/types";

/** 파라미터 한 줄의 입력값 (입력 중인 "-", "0." 등을 위해 문자열로 둔다) */
export interface ParamRow {
  optimize: boolean;
  low: string;
  high: string;
  step: string;
  value: string;
}

export const TRIAL_STATE_LABELS: Record<HyperoptTrialState, string> = {
  ok: "정상",
  few_trades: "거래 부족",
  no_score: "점수 없음",
  failed: "실패",
};

export const STOP_REASON_LABELS: Record<HyperoptStopReason, string> = {
  completed: "계획한 시행을 모두 마쳤습니다.",
  time_budget: "시간 제한에 닿아 새 시행을 멈추고 마무리했습니다.",
  space_exhausted: "시험할 새 조합이 거의 남지 않아 일찍 끝냈습니다.",
  cancelled: "중간에 멈췄습니다.",
};

export const PARAM_GROUP_LABELS: Record<HyperoptParamSpec["group"], string> = {
  param: "파라미터",
  indicator: "지표",
  condition: "조건",
  risk: "손절·익절",
};

// 학습·검증 구간의 최소 평일 수 (서버 MIN_TRAIN_WEEKDAYS / MIN_VALID_WEEKDAYS 와 같은 값)
export const MIN_TRAIN_WEEKDAYS = 10;
export const MIN_VALID_WEEKDAYS = 5;

function num(value: string): number {
  return value.trim() === "" ? Number.NaN : Number(value);
}

export function defaultRows(space: HyperoptSpace): Record<string, ParamRow> {
  return Object.fromEntries(
    space.params.map((spec) => [
      spec.name,
      {
        optimize: spec.suggested,
        low: String(spec.low),
        high: String(spec.high),
        step: String(spec.step),
        value: String(spec.default),
      },
    ])
  );
}

/** 입력이 잘못됐으면 이유, 괜찮으면 null */
export function rowError(spec: HyperoptParamSpec, row: ParamRow): string | null {
  const range = `${spec.min_allowed} ~ ${spec.max_allowed}`;
  if (!row.optimize) {
    const value = num(row.value);
    if (!Number.isFinite(value)) return "고정값을 숫자로 입력하세요.";
    if (value < spec.min_allowed || value > spec.max_allowed) return `고정값은 ${range} 안이어야 합니다.`;
    return null;
  }
  const low = num(row.low);
  const high = num(row.high);
  const step = num(row.step);
  if (![low, high, step].every(Number.isFinite)) return "최소·최대·간격을 숫자로 입력하세요.";
  if (low > high) return "최솟값이 최댓값보다 큽니다.";
  if (low < spec.min_allowed || high > spec.max_allowed) return `범위는 ${range} 안이어야 합니다.`;
  if (step <= 0) return "간격은 0보다 커야 합니다.";
  if (spec.type === "int" && !Number.isInteger(step)) return "정수 파라미터의 간격은 정수여야 합니다.";
  return null;
}

/** 범위 안에서 고를 수 있는 값의 개수 */
export function rowGridSize(row: ParamRow): number {
  const low = num(row.low);
  const high = num(row.high);
  const step = num(row.step);
  if (![low, high, step].every(Number.isFinite) || step <= 0 || low > high) return 0;
  return Math.floor((high - low) / step + 1e-9) + 1;
}

export function toChoices(space: HyperoptSpace, rows: Record<string, ParamRow>): HyperoptParamChoice[] {
  return space.params.map((spec) => {
    const row = rows[spec.name];
    if (!row) return { name: spec.name, optimize: false, value: spec.default };
    return row.optimize
      ? { name: spec.name, optimize: true, low: num(row.low), high: num(row.high), step: num(row.step) }
      : { name: spec.name, optimize: false, value: num(row.value) };
  });
}

export function formatParamValue(spec: HyperoptParamSpec | undefined, value: number | undefined | null): string {
  if (value === undefined || value === null || Number.isNaN(value)) return "-";
  if (!spec || spec.type === "int") return String(Math.round(value));
  return String(Number(value.toFixed(6)));
}

export function formatScore(score: number | null | undefined, digits = 3): string {
  return score === null || score === undefined || Number.isNaN(score) ? "-" : score.toFixed(digits);
}

/** 동시 실행으로 빨라지는 배수 (서버 parallel_speedup 과 같은 식) */
function parallelSpeedup(concurrency: number, perRun: number): number {
  const count = Math.max(concurrency, 1);
  if (perRun < 15) return 1 + 0.25 * (Math.min(count, 4) - 1) + 0.1 * Math.max(count - 4, 0);
  return 1 + 0.6 * (count - 1);
}

/** 하이퍼옵트 예상 시간 (서버 estimate_run_seconds / estimate_total_seconds 와 같은 식) */
export function estimateHyperopt(options: {
  symbolCount: number;
  startDate: string;
  endDate: string;
  validationRatio: number;
  resolution: BacktestResolution;
  barsPerDay: number;
  nTrials: number;
  concurrency: number;
  timeBudgetMinutes: number;
}): { perRun: number; total: number; trainWeekdays: number; validWeekdays: number } {
  const weekdays = countWeekdays(options.startDate, options.endDate);
  const validWeekdays = options.validationRatio > 0 ? Math.round(weekdays * options.validationRatio) : 0;
  const trainWeekdays = weekdays - validWeekdays;
  const perDay = options.resolution === "minute" ? options.barsPerDay : 1;
  const perRun = LEAN_STARTUP_SECONDS + (Math.max(options.symbolCount, 1) * trainWeekdays * perDay) / LEAN_BARS_PER_SECOND;
  const speedup = parallelSpeedup(options.concurrency, perRun);
  let total = ((options.nTrials + 1) * perRun) / speedup;
  if (validWeekdays > 0) total += perRun;
  total = Math.min(total, options.timeBudgetMinutes * 60 + perRun * 4);
  return { perRun, total, trainWeekdays, validWeekdays };
}
