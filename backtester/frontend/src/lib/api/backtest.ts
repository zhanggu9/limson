/**
 * 백테스트 API
 */

import { apiGet, apiPost } from "./client";
import type {
  BacktestResolution,
  BacktestRequest,
  BarMinutes,
  CustomBacktestRequest,
  BacktestResponse,
  MinuteCoverage,
} from "@/types";

/**
 * 분봉 데이터 범위 (DB에 있는 첫·마지막 거래일)와 분봉 백테스트 한도
 */
export async function getMinuteCoverage(): Promise<MinuteCoverage> {
  return apiGet("/api/backtest/minute-coverage");
}

/**
 * 백테스트 실행 (Preset 전략)
 */
export async function runBacktest(request: BacktestRequest): Promise<BacktestResponse> {
  return apiPost("/api/backtest/run", request);
}

/**
 * 커스텀 전략 백테스트 실행 (YAML)
 */
export async function runCustomBacktest(
  yamlContent: string,
  symbols: string[],
  startDate: string,
  endDate: string,
  initialCapital: number,
  commissionRate?: number,
  taxRate?: number,
  slippage?: number,
  resolution: BacktestResolution = "daily",
  minuteOptions?: { bar_minutes: BarMinutes; intraday_exit: string | null }
): Promise<BacktestResponse> {
  const request: CustomBacktestRequest = {
    yaml_content: yamlContent,
    symbols,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_rate: commissionRate,
    tax_rate: taxRate,
    slippage,
    resolution,
    ...minuteOptions,
  };
  return apiPost("/api/backtest/run-custom", request);
}
