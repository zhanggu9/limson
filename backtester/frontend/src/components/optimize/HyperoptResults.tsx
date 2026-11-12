"use client";

import { Fragment, useMemo, useState } from "react";
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
import type { HyperoptCandidate, HyperoptDoneEvent, HyperoptMetrics, HyperoptPeriodResult, HyperoptStartEvent } from "@/types";
import type { TrialEvent } from "./HyperoptProgress";

interface HyperoptResultsProps {
  start: HyperoptStartEvent;
  done: HyperoptDoneEvent;
  trials: TrialEvent[];
  onBacktest: (candidate: HyperoptCandidate) => void;
  onDownloadYaml: (candidate: HyperoptCandidate) => void;
}

/** 성과 줄: 학습·검증 기간에 똑같이 쓴다 */
const METRIC_ROWS: { key: string; label: string; render: (metrics: HyperoptMetrics, score: number | null | undefined) => React.ReactNode }[] = [
  { key: "score", label: "점수", render: (_, score) => <span className="font-semibold">{formatScore(score)}</span> },
  {
    key: "return",
    label: "수익률",
    render: (metrics) => (
      <span className={metrics.total_return >= 0 ? "text-profit" : "text-loss"}>{formatPercent(metrics.total_return)}</span>
    ),
  },
  { key: "drawdown", label: "최대 낙폭", render: (metrics) => `${metrics.max_drawdown.toFixed(1)}%` },
  { key: "trades", label: "매수 횟수", render: (metrics) => `${metrics.trades}회` },
];

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

  // 표의 줄: 탐색한 파라미터 순서대로 (목록에 없는 이름은 뒤에 붙인다)
  const paramNames = useMemo(() => {
    const names = specs.map((spec) => spec.name);
    for (const candidate of candidates) {
      for (const name of Object.keys(candidate.params)) if (!names.includes(name)) names.push(name);
    }
    return names.filter((name) => candidates.some((candidate) => name in candidate.params));
  }, [specs, candidates]);

  const periods: { key: string; title: string; range: string; pick: (c: HyperoptCandidate) => HyperoptPeriodResult | null }[] = [
    { key: "train", title: "학습 기간 성과", range: `${start.train.start} ~ ${start.train.end}`, pick: (c) => c.train },
    ...(start.valid
      ? [{ key: "valid", title: "검증 기간 성과", range: `${start.valid.start} ~ ${start.valid.end}`, pick: (c: HyperoptCandidate) => c.valid }]
      : []),
  ];

  const columnTone = (index: number) => (index === selected ? "bg-blue-50 dark:bg-blue-900/20" : "");
  const stickyCell = "sticky left-0 z-10 bg-white dark:bg-[#1e293b] pr-3";
  const groupHead = "pt-4 pb-1 text-left text-xs font-semibold text-slate-900 dark:text-white border-b border-slate-200 dark:border-slate-700";
  const rowHead = "py-1.5 pl-2 text-left text-xs font-normal text-slate-600 dark:text-slate-300 border-b border-slate-100 dark:border-slate-800";
  const valueCell = "py-1.5 px-3 text-center whitespace-nowrap border-b border-slate-100 dark:border-slate-800";

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
            세로 한 줄이 조합 하나입니다. 학습 기간 점수로 순위를 매긴 뒤, 학습에 안 쓴 검증 기간에서 다시 돌려 봤습니다. 검증에서도 기본값보다 좋아야 믿을 만한 값입니다.
            {baseline && (
              <>
                {" "}
                굵은 숫자는 기본값과 다른 설정값입니다 (<span className="font-semibold text-profit">▲ 빨강</span> 기본값보다 큼,{" "}
                <span className="font-semibold text-loss">▼ 파랑</span> 작음).
              </>
            )}
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-sm border-separate border-spacing-0">
              <thead>
                <tr>
                  <th className={cn(stickyCell, "w-56 min-w-[11rem] pb-2 text-left align-bottom text-xs font-medium text-slate-500")}>
                    순위를 누르면 아래 수익률 그래프가 바뀝니다
                  </th>
                  {candidates.map((candidate, index) => {
                    const better = verdict(candidate);
                    return (
                      <th key={`${candidate.label}-${candidate.number}`} className={cn("min-w-[8.5rem] p-0 align-top font-normal", columnTone(index), "rounded-t-lg")}>
                        <button
                          type="button"
                          onClick={() => setSelected(index)}
                          aria-pressed={index === selected}
                          className="w-full px-3 pt-2 pb-2 text-center"
                        >
                          <span className={cn("block text-base font-bold", candidate.rank === 1 ? "text-emerald-600" : "text-slate-900 dark:text-white")}>
                            {candidate.label}
                          </span>
                          {candidate.is_default && candidate.rank && <span className="block text-[11px] text-slate-400">= 기본값</span>}
                          {better !== null && (
                            <span
                              className={cn(
                                "mt-1 inline-block rounded-full px-2 py-0.5 text-[11px] font-medium",
                                better
                                  ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300"
                                  : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300"
                              )}
                            >
                              {better ? "검증도 기본값보다 좋음" : "검증은 기본값보다 나쁨"}
                            </span>
                          )}
                        </button>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th className={cn(stickyCell, groupHead)} scope="rowgroup">설정값</th>
                  {candidates.map((candidate, index) => (
                    <td key={candidate.number} className={cn(groupHead, columnTone(index))} />
                  ))}
                </tr>
                {paramNames.map((name) => {
                  const spec = specByName.get(name);
                  return (
                    <tr key={name}>
                      <th scope="row" className={cn(stickyCell, rowHead)}>{spec?.label ?? name}</th>
                      {candidates.map((candidate, index) => {
                        const value = candidate.params[name];
                        const base = baseline?.params[name];
                        const changed = value !== undefined && base !== undefined && candidate !== baseline && value !== base;
                        const higher = changed && value > (base as number);
                        return (
                          <td key={candidate.number} className={cn(valueCell, columnTone(index))}>
                            {value === undefined ? (
                              <span className="text-slate-400">-</span>
                            ) : (
                              <span
                                className={cn("font-mono", !changed ? "text-slate-500 dark:text-slate-400" : higher ? "font-bold text-profit" : "font-bold text-loss")}
                                title={changed ? `기본값 ${formatParamValue(spec, base as number)}에서 바뀜` : undefined}
                              >
                                {formatParamValue(spec, value)}
                                {changed && <span className="ml-1 text-[10px]" aria-hidden="true">{higher ? "▲" : "▼"}</span>}
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}

                {periods.map((period) => (
                  <Fragment key={period.key}>
                    <tr>
                      <th className={cn(stickyCell, groupHead)} scope="rowgroup">
                        {period.title}
                        <span className="ml-1 font-normal normal-case text-slate-400">{period.range}</span>
                      </th>
                      {candidates.map((candidate, index) => (
                        <td key={candidate.number} className={cn(groupHead, columnTone(index))} />
                      ))}
                    </tr>
                    {METRIC_ROWS.map((row) => (
                      <tr key={row.key}>
                        <th scope="row" className={cn(stickyCell, rowHead)}>
                          {row.key === "score" ? `점수 (${start.objective.label})` : row.label}
                        </th>
                        {candidates.map((candidate, index) => {
                          const result = period.pick(candidate);
                          return (
                            <td key={candidate.number} className={cn(valueCell, "font-mono", columnTone(index))}>
                              {result?.metrics ? row.render(result.metrics, result.score) : <span className="text-slate-400">-</span>}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </Fragment>
                ))}
                {!start.valid && (
                  <tr>
                    <th className={cn(stickyCell, groupHead)} scope="rowgroup">검증 기간 성과</th>
                    <td className={cn(groupHead, "text-center font-normal normal-case text-slate-400")} colSpan={candidates.length}>검증 안 함</td>
                  </tr>
                )}

                <tr>
                  <th className={cn(stickyCell, "pt-3")} />
                  {candidates.map((candidate, index) => (
                    <td key={candidate.number} className={cn("px-2 pt-3 pb-2 rounded-b-lg", columnTone(index))}>
                      <div className="flex flex-col items-stretch gap-1">
                        <button
                          type="button"
                          onClick={() => onBacktest(candidate)}
                          className="inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-md bg-kis-blue text-white text-xs font-semibold hover:bg-kis-blue-dark whitespace-nowrap"
                        >
                          <Play className="w-3 h-3" /> 이 값으로 백테스트
                        </button>
                        {candidate.yaml && (
                          <button
                            type="button"
                            onClick={() => onDownloadYaml(candidate)}
                            className="inline-flex items-center justify-center gap-1 px-2 py-1.5 rounded-md border border-slate-200 dark:border-slate-700 text-xs text-slate-600 dark:text-slate-300 hover:border-kis-blue hover:text-kis-blue whitespace-nowrap"
                          >
                            <Download className="w-3 h-3" /> YAML 받기
                          </button>
                        )}
                      </div>
                    </td>
                  ))}
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {chosen && chartData.length > 0 && (
          <div className="card">
            <h3 className="font-semibold text-slate-900 dark:text-white mb-1">{chosen.label} 누적 수익률</h3>
            <p className="text-xs text-slate-500 mb-2">위 표에서 순위를 누르면 바뀝니다. 학습·검증은 각각 0%에서 다시 시작합니다.</p>
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
