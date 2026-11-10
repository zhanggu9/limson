/**
 * 다른 화면(하이퍼옵트)에서 백테스트 화면으로 설정을 넘긴다 (브라우저 sessionStorage, 한 번 읽으면 지움).
 */

import type { BacktestResolution, BarMinutes } from "@/types";

const PREFILL_KEY = "limson.backtest.prefill";

export interface BacktestPrefill {
  strategyId?: string | null; // 프리셋 전략
  yaml?: string | null; // YAML 전략 (값을 넣은 YAML)
  symbols: string[];
  startDate: string;
  endDate: string;
  resolution: BacktestResolution;
  barMinutes: BarMinutes;
  intradayExit: string | null;
  initialCapital: number;
  commissionRate: number; // 화면 단위 (%)
  taxRate: number; // %
  slippage: number; // %
  params?: Record<string, number>; // 프리셋 파라미터 값
  note?: string; // 백테스트 화면 위쪽 안내
}

export function saveBacktestPrefill(prefill: BacktestPrefill): void {
  try {
    sessionStorage.setItem(PREFILL_KEY, JSON.stringify(prefill));
  } catch {
    // 저장 공간이 막힌 브라우저에서는 넘기지 않는다
  }
}

export function takeBacktestPrefill(): BacktestPrefill | null {
  try {
    const raw = sessionStorage.getItem(PREFILL_KEY);
    if (!raw) return null;
    sessionStorage.removeItem(PREFILL_KEY);
    const parsed = JSON.parse(raw) as BacktestPrefill;
    if (!parsed || !Array.isArray(parsed.symbols) || !parsed.startDate || !parsed.endDate) return null;
    return parsed;
  } catch {
    return null;
  }
}
