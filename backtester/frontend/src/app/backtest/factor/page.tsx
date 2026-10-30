"use client";

import { useCallback, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { AlertTriangle, Calendar, Layers, Loader2, Play, Sigma, TrendingUp } from "lucide-react";
import { StockInput } from "@/components/symbols";
import { runMomentumFactor } from "@/lib/api";
import { cn, formatPercent } from "@/lib/utils";
import type { MomentumFactorResult, RebalanceFrequency } from "@/types";

// 모멘텀 기간 프리셋 (거래일). 12-1: 최근 12개월 수익률에서 마지막 1개월 제외
const PRESETS = [
  { label: "12-1개월", lookback: 252, skip: 21 },
  { label: "6-1개월", lookback: 126, skip: 21 },
  { label: "3-0개월", lookback: 63, skip: 0 },
];

const REBALANCE_OPTIONS: { value: RebalanceFrequency; label: string }[] = [
  { value: "M", label: "월말" },
  { value: "W", label: "주말" },
];

// 분위 색: Q1(모멘텀 최저) 빨강 → Qn(최고) 파랑
function quantileColor(q: number, n: number): string {
  const t = n <= 1 ? 1 : (q - 1) / (n - 1);
  const hue = Math.round(0 + t * 220);
  return `hsl(${hue}, 70%, ${45 + (1 - Math.abs(t - 0.5) * 2) * 5}%)`;
}

function toDateInput(date: Date): string {
  return date.toISOString().split("T")[0];
}

function MetricCard({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "up" | "down" }) {
  return (
    <div className="card">
      <div className="text-xs font-medium text-slate-500 mb-1">{label}</div>
      <div
        className={cn(
          "text-xl font-bold tabular-nums",
          tone === "up" && "text-profit",
          tone === "down" && "text-loss",
          !tone && "text-slate-900 dark:text-white"
        )}
      >
        {value}
      </div>
      {hint && <div className="text-xs text-slate-400 mt-1">{hint}</div>}
    </div>
  );
}

const fmt = (value: number | null | undefined, digits = 2) =>
  value === null || value === undefined || Number.isNaN(value) ? "-" : value.toFixed(digits);

export default function MomentumFactorPage() {
  const [useAllSymbols, setUseAllSymbols] = useState(true);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 3);
    return toDateInput(d);
  });
  const [endDate, setEndDate] = useState(() => toDateInput(new Date()));
  const [lookback, setLookback] = useState(252);
  const [skip, setSkip] = useState(21);
  const [rebalance, setRebalance] = useState<RebalanceFrequency>("M");
  const [nQuantiles, setNQuantiles] = useState(5);
  const [commissionRate, setCommissionRate] = useState(0.015); // %
  const [taxRate, setTaxRate] = useState(0.2); // %
  const [slippage, setSlippage] = useState(0.1); // %

  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MomentumFactorResult | null>(null);

  const formError = useMemo(() => {
    if (!startDate || !endDate || startDate >= endDate) return "시작일은 종료일보다 앞이어야 합니다.";
    if (skip >= lookback) return "제외 기간은 모멘텀 기간보다 짧아야 합니다.";
    if (!useAllSymbols && symbols.length < nQuantiles) {
      return `종목을 ${nQuantiles}개 이상 선택하거나 'DB 전체 종목'을 켜세요.`;
    }
    return null;
  }, [startDate, endDate, skip, lookback, useAllSymbols, symbols.length, nQuantiles]);

  const handleRun = useCallback(async () => {
    setIsRunning(true);
    setError(null);
    try {
      const response = await runMomentumFactor({
        symbols: useAllSymbols ? [] : symbols,
        start_date: startDate,
        end_date: endDate,
        lookback,
        skip,
        rebalance,
        n_quantiles: nQuantiles,
        initial_capital: 100_000_000,
        commission_rate: commissionRate / 100,
        tax_rate: taxRate / 100,
        slippage: slippage / 100,
      });
      setResult(response.data);
    } catch (e) {
      setResult(null);
      setError(e instanceof Error ? e.message : "팩터 백테스트 실행 중 오류");
    } finally {
      setIsRunning(false);
    }
  }, [useAllSymbols, symbols, startDate, endDate, lookback, skip, rebalance, nQuantiles, commissionRate, taxRate, slippage]);

  // 분위별 자산곡선 + 동일가중을 하나의 차트 데이터로 합친다 (수익률 %)
  const chartData = useMemo(() => {
    if (!result) return [];
    const base = 100_000_000;
    const dates = Object.keys(result.benchmark.equity_curve);
    const step = Math.max(1, Math.floor(dates.length / 400));
    return dates
      .filter((_, i) => i % step === 0 || i === dates.length - 1)
      .map((date) => {
        const row: Record<string, number | string> = {
          date,
          benchmark: (result.benchmark.equity_curve[date] / base - 1) * 100,
        };
        result.quantiles.forEach((q) => {
          const v = q.equity_curve[date];
          if (v !== undefined) row[`q${q.quantile}`] = (v / base - 1) * 100;
        });
        return row;
      });
  }, [result]);

  const canRun = !formError && !isRunning;
  const n = result?.quantiles.length ?? nQuantiles;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white">모멘텀 팩터</h1>
        <p className="text-slate-600 dark:text-slate-400">
          종목을 모멘텀 순위로 분위 포트폴리오로 나눠 벡터화 백테스트합니다. PostgreSQL 일봉만 쓰며 몇 초 안에 끝납니다.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* 설정 */}
        <div className="space-y-4">
          <div className="card">
            <h3 className="font-semibold text-slate-900 dark:text-white mb-3 flex items-center gap-2">
              <Layers className="w-4 h-4 text-kis-blue" />
              유니버스
            </h3>
            <label className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300 mb-3">
              <input
                type="checkbox"
                checked={useAllSymbols}
                onChange={(e) => setUseAllSymbols(e.target.checked)}
              />
              DB 전체 종목 사용
            </label>
            {useAllSymbols ? (
              <p className="text-xs text-slate-400">
                현재 DB에 있는 종목만 대상이라 상장폐지 종목이 빠집니다 (생존 편향).
              </p>
            ) : (
              <StockInput stocks={symbols} onChange={setSymbols} />
            )}
          </div>

          <div className="card space-y-3">
            <h3 className="font-semibold text-slate-900 dark:text-white flex items-center gap-2">
              <Calendar className="w-4 h-4 text-kis-blue" />
              기간 · 리밸런싱
            </h3>
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-slate-500 mb-1 block">시작일</label>
                <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">종료일</label>
                <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block">리밸런싱 주기</label>
              <div role="radiogroup" aria-label="리밸런싱 주기"
                className="grid grid-cols-2 gap-1 p-1 rounded-lg bg-slate-100 dark:bg-slate-800">
                {REBALANCE_OPTIONS.map((option) => (
                  <button key={option.value} type="button" role="radio" aria-checked={rebalance === option.value}
                    onClick={() => setRebalance(option.value)}
                    className={cn("py-1.5 rounded-md text-sm font-medium transition-colors",
                      rebalance === option.value
                        ? "bg-white dark:bg-slate-700 text-kis-blue shadow-sm"
                        : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300")}>
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="card space-y-3">
            <h3 className="font-semibold text-slate-900 dark:text-white flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-kis-blue" />
              모멘텀 팩터
            </h3>
            <div className="flex flex-wrap gap-1">
              {PRESETS.map((p) => (
                <button key={p.label} type="button"
                  onClick={() => { setLookback(p.lookback); setSkip(p.skip); }}
                  className={cn("px-3 py-1 rounded-full text-xs border transition-colors",
                    lookback === p.lookback && skip === p.skip
                      ? "border-kis-blue text-kis-blue bg-blue-50 dark:bg-blue-900/20"
                      : "border-slate-200 dark:border-slate-700 text-slate-600 dark:text-slate-400")}>
                  {p.label}
                </button>
              ))}
            </div>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-xs text-slate-500 mb-1 block">기간(거래일)</label>
                <input type="number" min={20} max={504} value={lookback} onChange={(e) => setLookback(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">최근 제외</label>
                <input type="number" min={0} max={63} value={skip} onChange={(e) => setSkip(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">분위 수</label>
                <input type="number" min={2} max={10} value={nQuantiles} onChange={(e) => setNQuantiles(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
            </div>
            <p className="text-xs text-slate-400">
              모멘텀 = (최근 제외일 전 가격) ÷ (기간 전 가격) − 1. 매 리밸런싱일 종가로 순위를 매기고 다음 거래일부터 보유합니다.
            </p>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-xs text-slate-500 mb-1 block">수수료(%)</label>
                <input type="number" step="0.001" value={commissionRate} onChange={(e) => setCommissionRate(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">거래세(%)</label>
                <input type="number" step="0.01" value={taxRate} onChange={(e) => setTaxRate(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">슬리피지(%)</label>
                <input type="number" step="0.01" value={slippage} onChange={(e) => setSlippage(Number(e.target.value))}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm" />
              </div>
            </div>
          </div>

          {formError && <p role="alert" className="text-xs text-amber-600 dark:text-amber-400">{formError}</p>}
          <button onClick={handleRun} disabled={!canRun}
            className={cn("w-full flex items-center justify-center gap-2 py-3 rounded-xl font-bold transition-all",
              canRun ? "bg-kis-blue hover:bg-kis-blue-dark text-white shadow-lg" : "bg-slate-200 text-slate-400 cursor-not-allowed")}>
            {isRunning ? (<><Loader2 className="w-5 h-5 animate-spin" />계산 중...</>) : (<><Play className="w-5 h-5" />팩터 백테스트 실행</>)}
          </button>
        </div>

        {/* 결과 */}
        <div className="lg:col-span-2 space-y-4">
          {error && (
            <div className="card bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 flex items-start gap-3">
              <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
              <p className="text-sm text-red-700 dark:text-red-400">{error}</p>
            </div>
          )}

          {!result && !error && (
            <div className="card text-center py-16 text-slate-400">
              <Sigma className="w-10 h-10 mx-auto mb-3" />
              설정 후 실행하면 분위별 성과와 팩터 유효성(Rank IC)이 표시됩니다.
            </div>
          )}

          {result && (
            <>
              <p className="text-xs text-slate-500">
                {result.period.start} ~ {result.period.end} · 유니버스 {result.universe_size}종목 · 리밸런싱{" "}
                {result.rebalance_dates.length}회 · 계산 {result.timing_ms.load + result.timing_ms.compute}ms
                {result.missing_symbols.length > 0 && ` · 데이터 없음: ${result.missing_symbols.join(", ")}`}
              </p>

              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <MetricCard label={`전략 CAGR (Q${n})`} value={formatPercent(result.strategy.metrics.cagr)}
                  hint={`동일가중 ${formatPercent(result.benchmark.metrics.cagr)}`}
                  tone={result.strategy.metrics.cagr >= result.benchmark.metrics.cagr ? "up" : "down"} />
                <MetricCard label="샤프 / MDD" value={fmt(result.strategy.metrics.sharpe_ratio)}
                  hint={`MDD ${formatPercent(result.strategy.metrics.max_drawdown, false)}`} />
                <MetricCard label="평균 회전율" value={`${fmt(result.strategy.metrics.avg_turnover, 1)}%`}
                  hint="리밸런싱 1회당" />
                <MetricCard label={`롱숏 CAGR (Q${n}−Q1)`} value={formatPercent(result.long_short.metrics.cagr)}
                  hint="공매도 비용 미반영"
                  tone={result.long_short.metrics.cagr >= 0 ? "up" : "down"} />
              </div>

              <div className="card">
                <h3 className="font-semibold text-slate-900 dark:text-white mb-3">분위별 누적 수익률 (%)</h3>
                <ResponsiveContainer width="100%" height={320}>
                  <LineChart data={chartData} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={40} />
                    <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => `${Math.round(v)}%`} width={56} />
                    <Tooltip formatter={(v) => `${Number(v).toFixed(1)}%`} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    {result.quantiles.map((q) => (
                      <Line key={q.quantile} type="monotone" dataKey={`q${q.quantile}`} name={`Q${q.quantile}`}
                        stroke={quantileColor(q.quantile, n)} dot={false} isAnimationActive={false}
                        strokeWidth={q.quantile === n ? 2.5 : 1.5} />
                    ))}
                    <Line type="monotone" dataKey="benchmark" name="동일가중" stroke="#64748b"
                      strokeDasharray="5 4" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  </LineChart>
                </ResponsiveContainer>
                <p className="text-xs text-slate-400 mt-2">
                  Q1 = 모멘텀 최저, Q{n} = 최고. 팩터가 유효하면 분위 순서대로 선이 벌어집니다.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="card">
                  <h3 className="font-semibold text-slate-900 dark:text-white mb-3">분위별 성과</h3>
                  <table className="w-full text-sm tabular-nums">
                    <thead className="text-xs text-slate-500">
                      <tr><th className="text-left py-1">분위</th><th className="text-right">CAGR</th><th className="text-right">샤프</th><th className="text-right">MDD</th></tr>
                    </thead>
                    <tbody>
                      {result.quantiles.map((q) => (
                        <tr key={q.quantile} className="border-t border-slate-100 dark:border-slate-800">
                          <td className="py-1">
                            <span className="inline-block w-2 h-2 rounded-full mr-2" style={{ background: quantileColor(q.quantile, n) }} />
                            Q{q.quantile}
                          </td>
                          <td className="text-right">{formatPercent(q.metrics.cagr)}</td>
                          <td className="text-right">{fmt(q.metrics.sharpe_ratio)}</td>
                          <td className="text-right">{formatPercent(q.metrics.max_drawdown, false)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                <div className="card">
                  <h3 className="font-semibold text-slate-900 dark:text-white mb-1">팩터 유효성 (Rank IC)</h3>
                  <p className="text-xs text-slate-400 mb-3">리밸런싱마다 모멘텀 순위와 다음 기간 수익률 순위의 상관계수</p>
                  <dl className="grid grid-cols-2 gap-y-2 text-sm tabular-nums">
                    <dt className="text-slate-500">평균 IC</dt><dd className="text-right font-semibold">{fmt(result.ic.mean, 3)}</dd>
                    <dt className="text-slate-500">IC IR (평균/표준편차)</dt><dd className="text-right">{fmt(result.ic.ir)}</dd>
                    <dt className="text-slate-500">t-통계량</dt><dd className="text-right">{fmt(result.ic.t_stat)}</dd>
                    <dt className="text-slate-500">IC &gt; 0 비율</dt>
                    <dd className="text-right">{result.ic.positive_ratio === null ? "-" : `${(result.ic.positive_ratio * 100).toFixed(0)}%`}</dd>
                  </dl>
                  <p className="text-xs text-slate-400 mt-3">평균 IC 0.03 이상, t-통계량 2 이상이면 의미 있는 신호로 보는 편입니다.</p>
                </div>
              </div>

              <div className="card">
                <h3 className="font-semibold text-slate-900 dark:text-white mb-1">최신 모멘텀 순위 ({result.latest.date})</h3>
                <p className="text-xs text-slate-400 mb-3">다음 리밸런싱 때 상위 분위에 들어갈 후보입니다. LEAN 백테스트로 다시 검증하세요.</p>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {([["상위", result.latest.top.slice(0, 10)], ["하위", result.latest.bottom.slice(0, 10)]] as const).map(([title, rows]) => (
                    <table key={title} className="w-full text-sm tabular-nums">
                      <thead className="text-xs text-slate-500">
                        <tr><th className="text-left py-1">{title}</th><th className="text-right">모멘텀</th><th className="text-right">분위</th></tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => (
                          <tr key={row.symbol} className="border-t border-slate-100 dark:border-slate-800">
                            <td className="py-1 font-mono">{row.symbol}</td>
                            <td className="text-right">{formatPercent(row.momentum)}</td>
                            <td className="text-right">{row.quantile ? `Q${row.quantile}` : "-"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
