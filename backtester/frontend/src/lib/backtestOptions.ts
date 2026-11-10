/**
 * 백테스트·하이퍼옵트 화면이 같이 쓰는 설정값과 도우미
 */

import type { BacktestResolution, BarMinutes, Strategy } from "@/types";

export const DAY_MS = 24 * 60 * 60 * 1000;

export const RESOLUTION_OPTIONS: { value: BacktestResolution; label: string }[] = [
  { value: "daily", label: "일봉" },
  { value: "minute", label: "분봉" },
];

export const STRATEGY_CATEGORY_LABELS: Record<string, string> = {
  trend: "추세",
  momentum: "모멘텀",
  mean_reversion: "평균회귀",
  volatility: "변동성",
  composite: "복합",
};

export const BAR_MINUTE_OPTIONS: BarMinutes[] = [1, 3, 5, 10, 15, 30, 60];

// 예상 소요시간 (실측: LEAN 기동 약 5초 + 커스텀 봉 초당 약 3,500봉)
export const LEAN_STARTUP_SECONDS = 5;
export const LEAN_BARS_PER_SECOND = 3500;

export function toDateInput(date: Date): string {
  return date.toISOString().split("T")[0];
}

/** 시작일~종료일(포함)의 평일 수 — 서버 estimate_minute_bars 와 같은 기준 */
export function countWeekdays(start: string, end: string): number {
  let count = 0;
  for (let t = Date.parse(start); t <= Date.parse(end); t += DAY_MS) {
    const day = new Date(t).getUTCDay();
    if (day !== 0 && day !== 6) count += 1;
  }
  return count;
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `약 ${Math.max(1, Math.round(seconds))}초`;
  if (seconds < 3600) return `약 ${Math.round(seconds / 60)}분`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds - hours * 3600) / 60);
  return minutes > 0 ? `약 ${hours}시간 ${minutes}분` : `약 ${hours}시간`;
}

/** 경과 시간 표시 (예: 1분 05초) */
export function formatClock(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes > 0 ? `${minutes}분 ${String(rest).padStart(2, "0")}초` : `${rest}초`;
}

/** 전략 버튼: 카테고리별로 묶어 정해진 순서로 (모르는 카테고리는 뒤에 이름 그대로) */
export function groupStrategies(strategies: Strategy[]) {
  const order = ["trend", "momentum", "mean_reversion", "volatility", "composite"];
  const byCategory = new Map<string, Strategy[]>();
  for (const strategy of strategies) {
    const key = strategy.category || "기타";
    if (!byCategory.has(key)) byCategory.set(key, []);
    byCategory.get(key)!.push(strategy);
  }
  const rank = (c: string) => (order.indexOf(c) === -1 ? order.length : order.indexOf(c));
  return Array.from(byCategory.entries())
    .sort(([a], [b]) => rank(a) - rank(b))
    .map(([category, items]) => ({
      category,
      label: STRATEGY_CATEGORY_LABELS[category] ?? category,
      strategies: items,
    }));
}
