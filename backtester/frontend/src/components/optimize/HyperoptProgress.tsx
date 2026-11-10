"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Loader2, RotateCcw, Trophy } from "lucide-react";
import { cn, formatPercent } from "@/lib/utils";
import { formatClock, formatDuration } from "@/lib/backtestOptions";
import { TRIAL_STATE_LABELS, formatParamValue, formatScore } from "@/lib/hyperopt";
import type { HyperoptBest, HyperoptEvent, HyperoptParamSpec, HyperoptStartEvent } from "@/types";

export type TrialEvent = Extract<HyperoptEvent, { type: "trial" }>;

interface HyperoptProgressProps {
  start: HyperoptStartEvent | null;
  phase: "data" | "search" | "validate" | null;
  running: boolean;
  trials: TrialEvent[];
  best: HyperoptBest | null;
  elapsed: number;
  eta: number | null;
  validation: { done: number; total: number } | null;
  retries: number;
  finished: boolean; // 결과까지 받음
}

const STATE_COLORS = {
  ok: "#245bee",
  few_trades: "#94a3b8",
  baseline: "#f97316",
  best: "#10b981",
};

export function paramSummary(params: Record<string, number>, specs: HyperoptParamSpec[]): string {
  const byName = new Map(specs.map((spec) => [spec.name, spec]));
  return Object.entries(params)
    .map(([name, value]) => `${byName.get(name)?.label ?? name} ${formatParamValue(byName.get(name), value)}`)
    .join(" · ");
}

interface ScatterPoint {
  x: number;
  y: number;
  trial: TrialEvent;
}

function ScoreTooltip({ active, payload, specs }: { active?: boolean; payload?: { payload: ScatterPoint }[]; specs: HyperoptParamSpec[] }) {
  if (!active || !payload?.length) return null;
  const { trial } = payload[0].payload;
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-700 bg-white dark:bg-slate-800 px-3 py-2 text-xs shadow-lg max-w-xs">
      <p className="font-semibold text-slate-800 dark:text-slate-100">
        {trial.kind === "baseline" ? "기본값" : `시행 #${trial.number}`} · 점수 {formatScore(trial.score)}
      </p>
      {trial.metrics && (
        <p className="text-slate-500 mt-0.5">
          수익률 {formatPercent(trial.metrics.total_return)} · 최대낙폭 {trial.metrics.max_drawdown.toFixed(1)}% · 매수 {trial.metrics.trades}회
        </p>
      )}
      <p className="text-slate-600 dark:text-slate-300 mt-1">{paramSummary(trial.params, specs)}</p>
    </div>
  );
}

/** 진행 막대 + 현재 최고 기록 + 점수 분포 + 최근 시행 표 */
export function HyperoptProgress({ start, phase, running, trials, best, elapsed, eta, validation, retries, finished }: HyperoptProgressProps) {
  const specs = useMemo(() => start?.space ?? [], [start]);
  const searched = trials.filter((trial) => trial.kind === "trial").length;
  const total = start?.n_trials ?? 0;

  const points = useMemo(() => {
    const groups: Record<keyof typeof STATE_COLORS, ScatterPoint[]> = { ok: [], few_trades: [], baseline: [], best: [] };
    for (const trial of trials) {
      if (trial.score === null) continue;
      const point = { x: trial.number, y: trial.score, trial };
      if (trial.kind === "baseline") groups.baseline.push(point);
      else if (best && trial.number === best.number) groups.best.push(point);
      else if (trial.state === "ok") groups.ok.push(point);
      else groups.few_trades.push(point);
    }
    return groups;
  }, [trials, best]);

  const recent = useMemo(() => [...trials].reverse().slice(0, 200), [trials]);

  let status = "준비 중...";
  if (phase === "data") status = "PostgreSQL에서 주가 데이터를 준비하는 중...";
  else if (phase === "search") status = `파라미터를 바꿔 가며 백테스트하는 중 (시행 ${searched} / ${total})`;
  else if (phase === "validate") status = `학습에 안 쓴 뒤쪽 기간으로 검증하는 중 (${validation?.done ?? 0} / ${validation?.total ?? 0})`;
  if (!running) status = finished ? "끝났습니다." : "멈췄습니다.";

  const ratio = finished
    ? 1
    : phase === "validate" && validation
      ? 0.95 + 0.05 * (validation.total ? validation.done / validation.total : 0)
      : total
        ? Math.min(searched / total, 1) * 0.95
        : 0;

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
          <p className="text-sm font-medium text-slate-800 dark:text-slate-200 flex items-center gap-2">
            {running && <Loader2 className="w-4 h-4 animate-spin text-kis-blue" />}
            {status}
          </p>
          <p className="text-xs text-slate-500 tabular-nums">
            경과 {formatClock(elapsed)}
            {running && eta !== null && eta > 0 && ` · 남은 시간 ${formatDuration(eta)}`}
            {retries > 0 && (
              <span className="ml-2 inline-flex items-center gap-1 text-amber-600">
                <RotateCcw className="w-3 h-3" /> 응답 없어 다시 실행 {retries}회
              </span>
            )}
          </p>
        </div>
        <div
          className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2"
          role="progressbar"
          aria-label="하이퍼옵트 진행률"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(ratio * 100)}
        >
          <div className="bg-kis-blue h-2 rounded-full transition-all duration-500" style={{ width: `${Math.round(ratio * 100)}%` }} />
        </div>
        {best && (
          <div className="mt-3 flex items-start gap-2 text-sm">
            <Trophy className="w-4 h-4 text-emerald-500 mt-0.5 shrink-0" />
            <p className="text-slate-700 dark:text-slate-300">
              <span className="font-semibold">지금까지 최고</span> (시행 #{best.number}) {start?.objective.label}{" "}
              <span className="font-mono font-bold text-emerald-600">{formatScore(best.score)}</span>
              <span className="block text-xs text-slate-500 mt-0.5">{paramSummary(best.params, specs)}</span>
            </p>
          </div>
        )}
      </div>

      {trials.length > 0 && (
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="card">
            <h3 className="font-semibold text-slate-900 dark:text-white mb-1">시행별 점수</h3>
            <p className="text-xs text-slate-500 mb-2">
              점 하나가 백테스트 한 번입니다. 처음엔 무작위로 흩어 보고, 갈수록 점수가 높은 쪽을 집중해서 찾습니다.
            </p>
            <ResponsiveContainer width="100%" height={260}>
              <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis type="number" dataKey="x" name="시행" tick={{ fontSize: 11 }} allowDecimals={false} />
                <YAxis type="number" dataKey="y" name="점수" tick={{ fontSize: 11 }} />
                <Tooltip content={<ScoreTooltip specs={specs} />} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Scatter name="시행" data={points.ok} fill={STATE_COLORS.ok} isAnimationActive={false} />
                <Scatter name="거래 부족" data={points.few_trades} fill={STATE_COLORS.few_trades} isAnimationActive={false} />
                <Scatter name="기본값" data={points.baseline} fill={STATE_COLORS.baseline} shape="diamond" isAnimationActive={false} />
                <Scatter name="최고" data={points.best} fill={STATE_COLORS.best} shape="star" isAnimationActive={false} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>

          <div className="card">
            <h3 className="font-semibold text-slate-900 dark:text-white mb-2">최근 시행 ({trials.length}회)</h3>
            <div className="max-h-[290px] overflow-auto scrollbar-thin">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-white dark:bg-slate-800">
                  <tr className="text-left text-slate-500 border-b border-slate-200 dark:border-slate-700">
                    <th className="py-1.5 pr-2 whitespace-nowrap font-medium">#</th>
                    <th className="py-1.5 pr-2 whitespace-nowrap font-medium">결과</th>
                    <th className="py-1.5 pr-2 whitespace-nowrap font-medium text-right">점수</th>
                    <th className="py-1.5 pr-2 whitespace-nowrap font-medium text-right">수익률</th>
                    <th className="py-1.5 pr-2 whitespace-nowrap font-medium text-right">낙폭</th>
                    <th className="py-1.5 pr-2 whitespace-nowrap font-medium text-right">매수</th>
                    <th className="py-1.5 font-medium">값</th>
                  </tr>
                </thead>
                <tbody>
                  {recent.map((trial) => (
                    <tr
                      key={`${trial.kind}-${trial.number}`}
                      className={cn(
                        "border-b border-slate-100 dark:border-slate-800 last:border-0",
                        best && trial.kind === "trial" && trial.number === best.number && "bg-emerald-50 dark:bg-emerald-900/20"
                      )}
                    >
                      <td className="py-1.5 pr-2 font-mono text-slate-500 whitespace-nowrap">{trial.kind === "baseline" ? "기본" : trial.number}</td>
                      <td
                        className={cn(
                          "py-1.5 pr-2 whitespace-nowrap",
                          trial.state === "ok" ? "text-slate-700 dark:text-slate-300" : trial.state === "failed" ? "text-amber-600" : "text-slate-400"
                        )}
                        title={trial.error ?? undefined}
                      >
                        {TRIAL_STATE_LABELS[trial.state]}
                        {trial.attempts > 1 && " (재실행)"}
                      </td>
                      <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">{formatScore(trial.score)}</td>
                      <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">
                        {trial.metrics ? formatPercent(trial.metrics.total_return) : "-"}
                      </td>
                      <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">
                        {trial.metrics ? `${trial.metrics.max_drawdown.toFixed(1)}%` : "-"}
                      </td>
                      <td className="py-1.5 pr-2 text-right font-mono whitespace-nowrap">{trial.metrics ? trial.metrics.trades : "-"}</td>
                      <td className="py-1.5 text-slate-500">{paramSummary(trial.params, specs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
