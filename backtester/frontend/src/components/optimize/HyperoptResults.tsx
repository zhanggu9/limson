"use client";

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CheckCircle2, Download, Play, TriangleAlert } from "lucide-react";
import { cn, formatPercent } from "@/lib/utils";
import { formatClock } from "@/lib/backtestOptions";
import { STOP_REASON_LABELS, formatParamValue, formatScore } from "@/lib/hyperopt";
import type { HyperoptCandidate, HyperoptDoneEvent, HyperoptMetrics, HyperoptStartEvent } from "@/types";
import type { TrialEvent } from "./HyperoptProgress";

interface HyperoptResultsProps {
  start: HyperoptStartEvent;
  done: HyperoptDoneEvent;
  trials: TrialEvent[];
  onBacktest: (candidate: HyperoptCandidate) => void;
  onDownloadYaml: (candidate: HyperoptCandidate) => void;
}

function MetricCells({ metrics, score }: { metrics: HyperoptMetrics | null | undefined; score: number | null | undefined }) {
  if (!metrics) {
    return (
      <>
        <td className="py-2 pr-3 text-right text-slate-400" colSpan={4}>-</td>
      </>
    );
  }
  return (
    <>
      <td className="py-2 pr-3 text-right font-mono font-semibold">{formatScore(score)}</td>
      <td className={cn("py-2 pr-3 text-right font-mono", metrics.total_return >= 0 ? "text-profit" : "text-loss")}>
        {formatPercent(metrics.total_return)}
      </td>
      <td className="py-2 pr-3 text-right font-mono text-slate-600 dark:text-slate-400">{metrics.max_drawdown.toFixed(1)}%</td>
      <td className="py-2 pr-3 text-right font-mono text-slate-600 dark:text-slate-400">{metrics.trades}</td>
    </>
  );
}

/** 누적 수익률(%) 곡선: 각 구간의 첫 자산을 0%로 */
function returnSeries(curve: Record<string, number> | null | undefined): [string, number][] {
  if (!curve) return [];
  const entries = Object.entries(curve).sort(([a], [b]) => a.localeCompare(b));
  const base = entries[0]?.[1];
  if (!base) return [];
  return entries.map(([date, value]) => [date, ((value - base) / base) * 100]);
}

export function HyperoptResults({ start, done, trials, onBacktest, onDownloadYaml }: HyperoptResultsProps) {
  const specs = start.space;
  const specByName = useMemo(() => new Map(specs.map((spec) => [spec.name, spec])), [specs]);
  const candidates = done.candidates;
  const baseline = candidates.find((c) => c.is_default) ?? null;
  const [selected, setSelected] = useState(0);
  const chosen = candidates[Math.min(selected, candidates.length - 1)];

  // 중요도가 큰 파라미터부터
  const importance = useMemo(
    () => Object.entries(done.importance ?? {}).sort(([, a], [, b]) => b - a),
    [done.importance]
  );
  const [scatterParam, setScatterParam] = useState<string>(() => importance[0]?.[0] ?? specs[0]?.name ?? "");

  const chartData = useMemo(() => {
    if (!chosen) return [];
    const rows = new Map<string, { date: string; train?: number; valid?: number }>();
    for (const [date, value] of returnSeries(chosen.train.equity_curve)) rows.set(date, { date, train: value });
    for (const [date, value] of returnSeries(chosen.valid?.equity_curve)) rows.set(date, { ...(rows.get(date) ?? { date }), valid: value });
    return Array.from(rows.values()).sort((a, b) => a.date.localeCompare(b.date));
  }, [chosen]);

  const scatterData = useMemo(
    () =>
      trials
        .filter((t) => t.state === "ok" && t.score !== null && scatterParam in t.params)
        .map((t) => ({ x: t.params[scatterParam], y: t.score as number, number: t.number })),
    [trials, scatterParam]
  );

  const verdict = (candidate: HyperoptCandidate) => {
    if (candidate.is_default && !candidate.rank) return null;
    const valid = candidate.valid?.score;
    const base = baseline?.valid?.score;
    if (valid === null || valid === undefined || base === null || base === undefined || !baseline || baseline === candidate) return null;
    return valid > base;
  };

  const noWinner = candidates.every((c) => !c.rank);

  return (
    <div className="space-y-4">
      <div className="card">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="font-semibold text-slate-900 dark:text-white flex items-center gap-2">
              <CheckCircle2 className="w-5 h-5 text-emerald-500" />
              하이퍼옵트 결과 · {start.objective.label} 기준
            </h3>
            <p className="text-sm text-slate-600 dark:text-slate-400 mt-1">
              {STOP_REASON_LABELS[done.stopped_reason]} 걸린 시간 {formatClock(done.elapsed)} · 시행 {done.counts.trials}회 (정상{" "}
              {done.counts.ok}, 거래 부족 {done.counts.few_trades}
              {done.counts.failed > 0 && `, 실패 ${done.counts.failed}`})
              {done.counts.duplicates > 0 && ` · 같은 조합 ${done.counts.duplicates}번은 다시 돌리지 않음`}
            </p>
            <p className="text-xs text-slate-500 mt-1">
              학습 {start.train.start} ~ {start.train.end}
              {start.valid ? ` · 검증 ${start.valid.start} ~ ${start.valid.end} (학습에 안 쓴 기간)` : " · 검증 없음"}
            </p>
          </div>
        </div>
        {noWinner && (
          <p className="mt-3 text-sm text-amber-700 dark:text-amber-300 flex items-start gap-2">
            <TriangleAlert className="w-4 h-4 mt-0.5 shrink-0" />
            거래 수 기준을 넘긴 조합이 없습니다. 최소 거래 수를 낮추거나 기간을 늘려 보세요.
          </p>
        )}
      </div>

      {candidates.length > 0 && (
        <div className="card">
          <h3 className="font-semibold text-slate-900 dark:text-white mb-1">상위 조합과 기본값 비교</h3>
          <p className="text-xs text-slate-500 mb-3">
            학습 기간 점수로 순위를 매긴 뒤, 학습에 안 쓴 검증 기간에서 다시 돌려 봤습니다. 검증에서도 기본값보다 좋아야 믿을 만한 값입니다.
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-slate-500">
                  <th className="pb-1 pr-3 text-left font-medium" rowSpan={2}>순위</th>
                  <th className="pb-1 pr-3 text-left font-medium" rowSpan={2}>값</th>
                  <th className="pb-1 pr-3 text-center font-medium border-b border-slate-200 dark:border-slate-700" colSpan={4}>학습 기간</th>
                  <th className="pb-1 pr-3 text-center font-medium border-b border-slate-200 dark:border-slate-700" colSpan={4}>검증 기간</th>
                  <th className="pb-1 text-left font-medium" rowSpan={2}></th>
                </tr>
                <tr className="text-xs text-slate-500 border-b border-slate-200 dark:border-slate-700">
                  {["점수", "수익률", "낙폭", "매수", "점수", "수익률", "낙폭", "매수"].map((label, i) => (
                    <th key={i} className="pb-2 pr-3 text-right font-medium whitespace-nowrap">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {candidates.map((candidate, index) => {
                  const better = verdict(candidate);
                  return (
                    <tr
                      key={`${candidate.label}-${candidate.number}`}
                      onClick={() => setSelected(index)}
                      className={cn(
                        "border-b border-slate-100 dark:border-slate-800 last:border-0 cursor-pointer align-top",
                        index === selected ? "bg-blue-50/60 dark:bg-blue-900/20" : "hover:bg-slate-50 dark:hover:bg-slate-800/50"
                      )}
                    >
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <span className={cn("font-semibold", candidate.rank === 1 && "text-emerald-600")}>{candidate.label}</span>
                        {candidate.is_default && candidate.rank && <span className="block text-[11px] text-slate-400">= 기본값</span>}
                        {better !== null && (
                          <span className={cn("block text-[11px]", better ? "text-emerald-600" : "text-amber-600")}>
                            {better ? "검증도 기본값보다 좋음" : "검증은 기본값보다 나쁨"}
                          </span>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-xs text-slate-600 dark:text-slate-300 min-w-[12rem]">
                        {Object.entries(candidate.params).map(([name, value]) => (
                          <span key={name} className="inline-block mr-2 whitespace-nowrap">
                            <span className="text-slate-400">{specByName.get(name)?.label ?? name}</span>{" "}
                            <span className="font-mono">{formatParamValue(specByName.get(name), value)}</span>
                          </span>
                        ))}
                      </td>
                      <MetricCells metrics={candidate.train.metrics} score={candidate.train.score} />
                      {start.valid ? (
                        <MetricCells metrics={candidate.valid?.metrics} score={candidate.valid?.score} />
                      ) : (
                        <td className="py-2 pr-3 text-center text-xs text-slate-400" colSpan={4}>검증 안 함</td>
                      )}
                      <td className="py-2 whitespace-nowrap">
                        <div className="flex flex-col gap-1">
                          <button
                            type="button"
                            onClick={(e) => { e.stopPropagation(); onBacktest(candidate); }}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-kis-blue text-white text-xs font-semibold hover:bg-kis-blue-dark"
                          >
                            <Play className="w-3 h-3" /> 이 값으로 백테스트
                          </button>
                          {candidate.yaml && (
                            <button
                              type="button"
                              onClick={(e) => { e.stopPropagation(); onDownloadYaml(candidate); }}
                              className="inline-flex items-center gap-1 px-2 py-1 rounded-md border border-slate-200 dark:border-slate-700 text-xs text-slate-600 dark:text-slate-300 hover:border-kis-blue hover:text-kis-blue"
                            >
                              <Download className="w-3 h-3" /> YAML 받기
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {chosen && chartData.length > 0 && (
          <div className="card">
            <h3 className="font-semibold text-slate-900 dark:text-white mb-1">{chosen.label} 누적 수익률</h3>
            <p className="text-xs text-slate-500 mb-2">표에서 줄을 누르면 바뀝니다. 학습·검증은 각각 0%에서 다시 시작합니다.</p>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={chartData} margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
                <YAxis tick={{ fontSize: 11 }} tickFormatter={(v: number) => `${v.toFixed(0)}%`} />
                <Tooltip formatter={(value) => (typeof value === "number" ? `${value.toFixed(2)}%` : String(value))} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <ReferenceLine y={0} stroke="#94a3b8" />
                <Line type="monotone" dataKey="train" name="학습 기간" stroke="#245bee" dot={false} strokeWidth={2} isAnimationActive={false} connectNulls />
                {start.valid && (
                  <Line type="monotone" dataKey="valid" name="검증 기간" stroke="#10b981" dot={false} strokeWidth={2} isAnimationActive={false} connectNulls />
                )}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        <div className="card">
          <h3 className="font-semibold text-slate-900 dark:text-white mb-1">파라미터별 점수</h3>
          <p className="text-xs text-slate-500 mb-2">
            {importance.length > 0
              ? "영향이 큰 파라미터일수록 값에 따라 점수가 크게 달라집니다."
              : "값에 따라 점수가 어떻게 바뀌는지 봅니다."}
          </p>
          {importance.length > 0 && (
            <div className="space-y-1.5 mb-3">
              {importance.map(([name, value]) => (
                <div key={name} className="flex items-center gap-2 text-xs">
                  <span className="w-40 shrink-0 truncate text-slate-600 dark:text-slate-300" title={specByName.get(name)?.label ?? name}>
                    {specByName.get(name)?.label ?? name}
                  </span>
                  <div className="flex-1 h-2 rounded-full bg-slate-100 dark:bg-slate-800">
                    <div className="h-2 rounded-full bg-kis-blue" style={{ width: `${Math.max(2, value * 100)}%` }} />
                  </div>
                  <span className="w-10 text-right font-mono text-slate-500">{Math.round(value * 100)}%</span>
                </div>
              ))}
            </div>
          )}
          <div className="flex items-center gap-2 mb-2">
            <label className="text-xs text-slate-500" htmlFor="scatter-param">파라미터</label>
            <select
              id="scatter-param"
              value={scatterParam}
              onChange={(e) => setScatterParam(e.target.value)}
              className="px-2 py-1 border border-slate-200 dark:border-slate-700 rounded-md text-xs bg-white dark:bg-slate-900"
            >
              {specs.map((spec) => (
                <option key={spec.name} value={spec.name}>{spec.label}</option>
              ))}
            </select>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis type="number" dataKey="x" name={specByName.get(scatterParam)?.label ?? scatterParam} tick={{ fontSize: 11 }} domain={["auto", "auto"]} />
              <YAxis type="number" dataKey="y" name="점수" tick={{ fontSize: 11 }} />
              <Tooltip cursor={{ strokeDasharray: "3 3" }} />
              <Scatter data={scatterData} fill="#245bee" isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
