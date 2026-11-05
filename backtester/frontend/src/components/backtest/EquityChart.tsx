"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AreaChart,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  ReferenceDot,
} from "recharts";
import { RotateCcw } from "lucide-react";
import { cn, formatCurrency, formatPercent } from "@/lib/utils";

/* eslint-disable @typescript-eslint/no-explicit-any */

/** Chart color constants */
export const CHART_COLORS = {
  strategy: "#245bee",
  benchmark: "#f59e0b",
  buy: "#22c55e",
  sell: "#ef4444",
  drawdown: "#ef4444",
  grid: "#e2e8f0",
  reference: "#94a3b8",
} as const;

/** 종목 가격선 색 (전략 파랑·KOSPI 주황·매수 초록·매도 빨강과 겹치지 않게) */
const SYMBOL_COLORS = [
  "#8b5cf6", "#14b8a6", "#ec4899", "#64748b", "#06b6d4",
  "#a16207", "#84cc16", "#d946ef", "#0f766e", "#9f1239",
];

/** 종목이 이보다 많으면 처음에는 앞의 몇 개만 켠다 (범례에서 켜고 끌 수 있음) */
const SYMBOL_DEFAULT_VISIBLE = 3;

/** 확대했을 때 최소로 보이는 봉 수 */
const MIN_VISIBLE_POINTS = 10;
/** 휠 한 칸당 확대 비율 */
const ZOOM_STEP = 0.8;
/** 메인 차트 Y축 폭 / 오른쪽 여백 (커서 위치 → 데이터 위치 계산용) */
const Y_AXIS_WIDTH = 70;
const RIGHT_MARGIN = 10;

export interface ChartDataPoint {
  date: string;
  value: number;
  returnPct: number;
  benchmarkPct: number | null;
  benchmark: number | null;
  drawdown: number;
  /** 종목별 초기자본 환산 금액 (처음에 그 종목만 샀다면) */
  symbolValues?: Record<string, number | null>;
  /** 종목별 실제 종가 */
  symbolCloses?: Record<string, number | null>;
}

export interface TradeMarker {
  date: string;
  type: "buy" | "sell";
  price: number;
}

/** Grouped marker for rendering (same date trades aggregated) */
interface GroupedMarker {
  date: string;
  type: "buy" | "sell";
  count: number;
}

interface SymbolSeries {
  code: string;
  label: string;
  color: string;
}

interface EquityChartProps {
  chartData: ChartDataPoint[];
  tradeMarkers: TradeMarker[];
  initialCapital: number;
  /** 종목 가격선 순서 (chartData.symbolValues의 키) */
  symbolCodes?: string[];
  /** 종목코드 → 종목명 */
  symbolNames?: Record<string, string>;
}

/** Format axis values in Korean units (억/만) */
function formatAxisValue(v: number): string {
  if (v >= 100_000_000) return `${(v / 100_000_000).toFixed(1)}억`;
  return `${Math.round(v / 10_000).toLocaleString()}만`;
}

/** Format drawdown axis (%) */
function formatDrawdownAxis(v: number): string {
  return `${v.toFixed(0)}%`;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(Math.max(v, lo), hi);
}

/** Custom tooltip for the equity chart */
function EquityTooltipInner({
  active,
  payload,
  tradeMarkers,
  symbolSeries,
  initialCapital,
}: {
  active?: boolean;
  payload?: readonly any[];
  tradeMarkers: TradeMarker[];
  symbolSeries: SymbolSeries[];
  initialCapital: number;
}) {
  if (!active || !payload?.length) return null;

  const data = payload[0].payload as ChartDataPoint;
  const trades = tradeMarkers.filter((t) => t.date === data.date);
  const excessReturn =
    data.benchmarkPct !== null ? data.returnPct - data.benchmarkPct : null;

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg p-3 shadow-lg min-w-[200px]">
      <p className="text-xs text-slate-500 font-medium mb-2 border-b border-slate-100 dark:border-slate-700 pb-1">
        {data.date}
      </p>

      {/* Strategy */}
      <div className="flex items-center gap-2 mb-1">
        <span
          className="w-2 h-2 rounded-full flex-shrink-0"
          style={{ backgroundColor: CHART_COLORS.strategy }}
        />
        <span className="text-xs text-slate-500">전략</span>
        <span className="text-xs font-semibold text-slate-900 dark:text-white ml-auto tabular-nums">
          {formatCurrency(data.value)}
        </span>
      </div>

      {/* Benchmark */}
      {data.benchmark !== null && (
        <div className="flex items-center gap-2 mb-1">
          <span
            className="w-2 h-2 rounded-full flex-shrink-0"
            style={{ backgroundColor: CHART_COLORS.benchmark }}
          />
          <span className="text-xs text-slate-500">KOSPI</span>
          <span className="text-xs font-semibold text-slate-900 dark:text-white ml-auto tabular-nums">
            {formatCurrency(data.benchmark)}
          </span>
        </div>
      )}

      {/* 종목: 실제 종가 + 시작일 대비 수익률 */}
      {symbolSeries.map((s) => {
        const close = data.symbolCloses?.[s.code];
        const value = data.symbolValues?.[s.code];
        if (close == null || value == null) return null;
        const pct = ((value - initialCapital) / initialCapital) * 100;
        return (
          <div key={s.code} className="flex items-center gap-2 mb-1">
            <span
              className="w-2 h-2 rounded-full flex-shrink-0"
              style={{ backgroundColor: s.color }}
            />
            <span className="text-xs text-slate-500 truncate max-w-[90px]">{s.label}</span>
            <span className="text-xs font-semibold text-slate-900 dark:text-white ml-auto tabular-nums">
              {formatCurrency(close)}
            </span>
            <span className={cn("text-[10px] tabular-nums w-12 text-right", pct >= 0 ? "text-profit" : "text-loss")}>
              {formatPercent(pct)}
            </span>
          </div>
        );
      })}

      {/* Returns */}
      <div className="mt-2 pt-1.5 border-t border-slate-100 dark:border-slate-700 space-y-0.5">
        <div className="flex justify-between">
          <span className="text-xs text-slate-500">수익률</span>
          <span
            className={cn(
              "text-xs font-medium tabular-nums",
              data.returnPct >= 0 ? "text-profit" : "text-loss"
            )}
          >
            {formatPercent(data.returnPct)}
          </span>
        </div>
        {data.benchmarkPct !== null && (
          <div className="flex justify-between">
            <span className="text-xs text-slate-500">KOSPI</span>
            <span className="text-xs text-slate-400 tabular-nums">
              {formatPercent(data.benchmarkPct)}
            </span>
          </div>
        )}
        {excessReturn !== null && (
          <div className="flex justify-between">
            <span className="text-xs text-slate-500">초과수익</span>
            <span
              className={cn(
                "text-xs font-bold tabular-nums",
                excessReturn >= 0 ? "text-profit" : "text-loss"
              )}
            >
              {formatPercent(excessReturn)}
            </span>
          </div>
        )}
        {data.drawdown < 0 && (
          <div className="flex justify-between">
            <span className="text-xs text-slate-500">낙폭</span>
            <span className="text-xs text-loss tabular-nums">
              {data.drawdown.toFixed(2)}%
            </span>
          </div>
        )}
      </div>

      {/* Trade markers */}
      {trades.length > 0 && (
        <div className="mt-2 pt-1.5 border-t border-slate-100 dark:border-slate-700">
          {trades.map((trade, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{
                  backgroundColor:
                    trade.type === "buy"
                      ? CHART_COLORS.buy
                      : CHART_COLORS.sell,
                }}
              />
              <span className="text-xs font-medium">
                {trade.type === "buy" ? "매수" : "매도"}
              </span>
              <span className="text-xs text-slate-400 ml-auto tabular-nums">
                {formatCurrency(trade.price)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Drawdown tooltip */
function DrawdownTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: readonly any[];
}) {
  if (!active || !payload?.length) return null;
  const data = payload[0].payload as ChartDataPoint;

  return (
    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg px-3 py-2 shadow-lg">
      <p className="text-xs text-slate-500">{data.date}</p>
      <p className="text-sm font-semibold text-loss tabular-nums">
        {data.drawdown.toFixed(2)}%
      </p>
    </div>
  );
}

/** Chart legend (종목은 눌러서 켜고 끈다) */
function ChartLegend({
  hasBenchmark,
  hasTrades,
  symbolSeries,
  hiddenSymbols,
  onToggleSymbol,
}: {
  hasBenchmark: boolean;
  hasTrades: boolean;
  symbolSeries: SymbolSeries[];
  hiddenSymbols: Set<string>;
  onToggleSymbol: (code: string) => void;
}) {
  return (
    <div className="flex items-center gap-x-4 gap-y-1 flex-wrap justify-end">
      <div className="flex items-center gap-1.5">
        <span
          className="w-3 h-0.5 rounded-full"
          style={{ backgroundColor: CHART_COLORS.strategy }}
        />
        <span className="text-xs text-slate-500">전략</span>
      </div>
      {hasBenchmark && (
        <div className="flex items-center gap-1.5">
          <span
            className="w-3 h-0.5 rounded-full"
            style={{ backgroundColor: CHART_COLORS.benchmark }}
          />
          <span className="text-xs text-slate-500">KOSPI</span>
        </div>
      )}
      {symbolSeries.map((s) => {
        const hidden = hiddenSymbols.has(s.code);
        return (
          <button
            key={s.code}
            type="button"
            onClick={() => onToggleSymbol(s.code)}
            className={cn(
              "flex items-center gap-1.5 rounded px-1 -mx-1 hover:bg-slate-100 dark:hover:bg-slate-800",
              hidden && "opacity-40"
            )}
            title={hidden ? "눌러서 표시" : "눌러서 숨기기"}
          >
            <span className="w-3 h-0.5 rounded-full" style={{ backgroundColor: s.color }} />
            <span className={cn("text-xs text-slate-500", hidden && "line-through")}>{s.label}</span>
          </button>
        );
      })}
      {hasTrades && (
        <>
          <div className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: CHART_COLORS.buy }}
            />
            <span className="text-xs text-slate-500">매수</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span
              className="w-2 h-2 rounded-full"
              style={{ backgroundColor: CHART_COLORS.sell }}
            />
            <span className="text-xs text-slate-500">매도</span>
          </div>
        </>
      )}
    </div>
  );
}

export function EquityChart({
  chartData,
  tradeMarkers,
  initialCapital,
  symbolCodes = [],
  symbolNames = {},
}: EquityChartProps) {
  const total = chartData.length;

  // ── 종목 가격선 ──────────────────────────────────────────
  const symbolSeries: SymbolSeries[] = useMemo(
    () =>
      symbolCodes.map((code, i) => ({
        code,
        label: symbolNames[code] ? `${symbolNames[code]}` : code,
        color: SYMBOL_COLORS[i % SYMBOL_COLORS.length],
      })),
    [symbolCodes, symbolNames]
  );
  // 숨긴 종목은 결과(symbolCodes)마다 따로 둔다. 새 결과가 오면 앞의 몇 개만 켠 상태로 시작한다.
  const [hiddenState, setHiddenState] = useState<{ codes: string[]; hidden: Set<string> } | null>(null);
  const defaultHidden = useMemo(
    () => new Set(symbolCodes.slice(SYMBOL_DEFAULT_VISIBLE)),
    [symbolCodes]
  );
  const hiddenSymbols = hiddenState?.codes === symbolCodes ? hiddenState.hidden : defaultHidden;
  const toggleSymbol = useCallback((code: string) => {
    const next = new Set(hiddenSymbols);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    setHiddenState({ codes: symbolCodes, hidden: next });
  }, [hiddenSymbols, symbolCodes]);
  const visibleSymbols = useMemo(
    () => symbolSeries.filter((s) => !hiddenSymbols.has(s.code)),
    [symbolSeries, hiddenSymbols]
  );

  // ── 확대·이동 구간 [start, end] (chartData 인덱스, 양끝 포함) ──
  // 구간은 결과(chartData)마다 따로 둔다. 새 결과가 오면 전체 구간으로 시작한다.
  type ZoomState = { data: ChartDataPoint[]; range: [number, number] };
  const fullRange = useMemo((): [number, number] => [0, Math.max(0, total - 1)], [total]);
  const [zoomState, setZoomState] = useState<ZoomState | null>(null);
  const range = zoomState?.data === chartData ? zoomState.range : fullRange;
  // 휠·드래그 이벤트가 연달아 와도 최신 구간을 쓰도록 ref에도 같이 둔다
  const rangeRef = useRef<ZoomState | null>(null);
  const currentRange = useCallback(
    (): [number, number] =>
      rangeRef.current?.data === chartData ? rangeRef.current.range : fullRange,
    [chartData, fullRange]
  );
  const setRange = useCallback((next: [number, number]) => {
    const state = { data: chartData, range: next };
    rangeRef.current = state;
    setZoomState(state);
  }, [chartData]);

  const [start, end] = range;
  const isZoomed = start > 0 || end < total - 1;
  const visibleData = useMemo(
    () => chartData.slice(start, end + 1),
    [chartData, start, end]
  );

  const plotRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; range: [number, number]; width: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  // 휠: 커서 위치를 중심으로 확대·축소 / 가로 휠·Shift+휠: 좌우 이동
  useEffect(() => {
    const el = plotRef.current;
    if (!el || total < 2) return;

    const onWheel = (e: WheelEvent) => {
      const [s, en] = currentRange();
      const span = en - s + 1;
      const rect = el.getBoundingClientRect();
      const plotWidth = Math.max(1, rect.width - Y_AXIS_WIDTH - RIGHT_MARGIN);
      const horizontal = e.shiftKey || Math.abs(e.deltaX) > Math.abs(e.deltaY);

      if (horizontal) {
        if (span >= total) return;  // 전체가 보이면 이동할 곳이 없다
        e.preventDefault();
        const delta = e.shiftKey ? e.deltaY : e.deltaX;
        const shift = Math.round((delta / plotWidth) * span) || Math.sign(delta);
        const ns = clamp(s + shift, 0, total - span);
        setRange([ns, ns + span - 1]);
        return;
      }

      const zoomIn = e.deltaY < 0;
      if (!zoomIn && span >= total) return;  // 다 축소된 상태면 페이지 스크롤을 그대로 둔다
      e.preventDefault();

      const frac = clamp((e.clientX - rect.left - Y_AXIS_WIDTH) / plotWidth, 0, 1);
      const newSpan = clamp(
        Math.round(zoomIn ? span * ZOOM_STEP : span / ZOOM_STEP),
        Math.min(MIN_VISIBLE_POINTS, total),
        total
      );
      if (newSpan === span) return;
      const anchor = s + frac * (span - 1);
      const ns = clamp(Math.round(anchor - frac * (newSpan - 1)), 0, total - newSpan);
      setRange([ns, ns + newSpan - 1]);
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [total, setRange, currentRange]);

  // 드래그: 좌우 이동
  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    if (e.button !== 0 || !isZoomed || !plotRef.current) return;
    const rect = plotRef.current.getBoundingClientRect();
    dragRef.current = {
      x: e.clientX,
      range: currentRange(),
      width: Math.max(1, rect.width - Y_AXIS_WIDTH - RIGHT_MARGIN),
    };
    setIsDragging(true);
    e.preventDefault();
  }, [isZoomed, currentRange]);

  useEffect(() => {
    if (!isDragging) return;
    const onMove = (e: MouseEvent) => {
      const drag = dragRef.current;
      if (!drag) return;
      const [s0, e0] = drag.range;
      const span = e0 - s0 + 1;
      const shift = Math.round(((drag.x - e.clientX) / drag.width) * span);
      const ns = clamp(s0 + shift, 0, total - span);
      setRange([ns, ns + span - 1]);
    };
    const onUp = () => {
      dragRef.current = null;
      setIsDragging(false);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [isDragging, total, setRange]);

  const resetZoom = useCallback(() => setRange(fullRange), [fullRange, setRange]);

  // ── 보이는 구간 기준 축 ──────────────────────────────────
  const hasBenchmark = useMemo(
    () => chartData.some((d) => d.benchmark !== null),
    [chartData]
  );
  const hasTrades = tradeMarkers.length > 0;

  /** Y축: 보이는 구간의 전략·KOSPI·켜진 종목 값 + 초기자본, 15% 여백 */
  const yAxisDomain = useMemo((): [number, number] => {
    const values: number[] = [initialCapital];
    for (const d of visibleData) {
      values.push(d.value);
      if (d.benchmark !== null) values.push(d.benchmark);
      for (const s of visibleSymbols) {
        const v = d.symbolValues?.[s.code];
        if (v != null) values.push(v);
      }
    }
    const min = Math.min(...values);
    const max = Math.max(...values);
    const padding = (max - min) * 0.15 || max * 0.05 || 1;
    return [Math.max(0, min - padding), max + padding];
  }, [visibleData, visibleSymbols, initialCapital]);

  const visibleIndex = useMemo(() => {
    const map = new Map<string, ChartDataPoint>();
    for (const d of visibleData) map.set(d.date, d);
    return map;
  }, [visibleData]);

  // Group markers by (date, type) → count per group, split buy/sell on same date
  const groupedMarkers: GroupedMarker[] = useMemo(() => {
    const map = new Map<string, number>();
    for (const m of tradeMarkers) {
      if (!visibleIndex.has(m.date)) continue;
      const key = `${m.date}|${m.type}`;
      map.set(key, (map.get(key) || 0) + 1);
    }
    return Array.from(map.entries()).map(([key, count]) => {
      const [date, type] = key.split("|");
      return { date, type: type as "buy" | "sell", count };
    });
  }, [tradeMarkers, visibleIndex]);

  const markerDates = useMemo(() => {
    const byDate = new Map<string, Set<string>>();
    for (const m of groupedMarkers) {
      if (!byDate.has(m.date)) byDate.set(m.date, new Set());
      byDate.get(m.date)!.add(m.type);
    }
    return byDate;
  }, [groupedMarkers]);

  const xInterval = useMemo(
    () => Math.max(0, Math.floor(visibleData.length / 8) - 1),
    [visibleData.length]
  );

  /** Drawdown Y-axis domain (always negative) */
  const drawdownDomain = useMemo(() => {
    const minDD = Math.min(0, ...visibleData.map((d) => d.drawdown));
    const padding = Math.abs(minDD) * 0.15;
    return [minDD - padding, 0];
  }, [visibleData]);

  // 툴팁 렌더 함수 (거래·종목·초기자본을 클로저로 받는다)
  const renderEquityTooltip = useCallback(
    (props: any) => (
      <EquityTooltipInner
        {...props}
        tradeMarkers={tradeMarkers}
        symbolSeries={visibleSymbols}
        initialCapital={initialCapital}
      />
    ),
    [tradeMarkers, visibleSymbols, initialCapital]
  );

  const visibleCount = end - start + 1;

  return (
    <div className="card">
      <div className="flex items-start justify-between gap-4 mb-2">
        <h3 className="font-semibold text-slate-900 dark:text-white whitespace-nowrap">
          자산 추이
        </h3>
        <ChartLegend
          hasBenchmark={hasBenchmark}
          hasTrades={hasTrades}
          symbolSeries={symbolSeries}
          hiddenSymbols={hiddenSymbols}
          onToggleSymbol={toggleSymbol}
        />
      </div>
      <div className="flex items-center justify-between gap-2 mb-2 text-[11px] text-slate-400">
        <span>
          {isZoomed
            ? `${visibleData[0]?.date} ~ ${visibleData[visibleData.length - 1]?.date} (${visibleCount}/${total}일)`
            : "휠: 확대·축소 · 드래그 / Shift+휠: 좌우 이동"}
          {symbolSeries.length > 0 && " · 종목선은 처음에 그 종목만 샀을 때의 금액"}
        </span>
        {isZoomed && (
          <button
            type="button"
            onClick={resetZoom}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded border border-slate-200 dark:border-slate-700 text-slate-500 hover:bg-slate-50 dark:hover:bg-slate-800"
          >
            <RotateCcw className="w-3 h-3" />
            전체 보기
          </button>
        )}
      </div>

      {/* Main equity chart */}
      <div
        ref={plotRef}
        onMouseDown={handleMouseDown}
        onDoubleClick={resetZoom}
        className={cn(
          "h-[380px] select-none",
          isZoomed && (isDragging ? "cursor-grabbing" : "cursor-grab")
        )}
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={visibleData}
            margin={{ top: 10, right: RIGHT_MARGIN, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id="gradStrategy" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="5%"
                  stopColor={CHART_COLORS.strategy}
                  stopOpacity={0.3}
                />
                <stop
                  offset="95%"
                  stopColor={CHART_COLORS.strategy}
                  stopOpacity={0}
                />
              </linearGradient>
              <linearGradient id="gradBenchmark" x1="0" y1="0" x2="0" y2="1">
                <stop
                  offset="5%"
                  stopColor={CHART_COLORS.benchmark}
                  stopOpacity={0.2}
                />
                <stop
                  offset="95%"
                  stopColor={CHART_COLORS.benchmark}
                  stopOpacity={0}
                />
              </linearGradient>
            </defs>
            <CartesianGrid
              strokeDasharray="3 3"
              stroke={CHART_COLORS.grid}
            />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10 }}
              tickFormatter={(v: string) => v.slice(5)}
              interval={xInterval}
            />
            <YAxis
              tick={{ fontSize: 10 }}
              tickFormatter={formatAxisValue}
              domain={yAxisDomain}
              allowDataOverflow
              width={Y_AXIS_WIDTH}
            />
            <Tooltip content={renderEquityTooltip} />
            <ReferenceLine
              y={initialCapital}
              stroke={CHART_COLORS.reference}
              strokeDasharray="3 3"
              label={{
                value: "초기자본",
                position: "insideTopRight",
                fontSize: 10,
                fill: CHART_COLORS.reference,
              }}
            />
            {/* KOSPI benchmark (behind) */}
            {hasBenchmark && (
              <Area
                type="monotone"
                dataKey="benchmark"
                stroke={CHART_COLORS.benchmark}
                fill="url(#gradBenchmark)"
                name="KOSPI"
                connectNulls
                isAnimationActive={false}
              />
            )}
            {/* 종목 가격선 (초기자본 환산) */}
            {visibleSymbols.map((s) => (
              <Line
                key={s.code}
                type="monotone"
                dataKey={(d: ChartDataPoint) => d.symbolValues?.[s.code] ?? null}
                name={s.label}
                stroke={s.color}
                strokeWidth={1.3}
                dot={false}
                activeDot={{ r: 3 }}
                connectNulls
                isAnimationActive={false}
              />
            ))}
            {/* Strategy equity (front) */}
            <Area
              type="monotone"
              dataKey="value"
              stroke={CHART_COLORS.strategy}
              fill="url(#gradStrategy)"
              name="전략"
              isAnimationActive={false}
            />
            {/* Buy/Sell markers (grouped by date+type) */}
            {groupedMarkers.map((marker, idx) => {
              const point = visibleIndex.get(marker.date);
              if (!point) return null;

              // Check if both buy and sell exist on the same date
              const hasBoth = (markerDates.get(marker.date)?.size ?? 0) > 1;
              // Offset y when both types on same date (buy slightly above, sell slightly below)
              const yRange = yAxisDomain[1] - yAxisDomain[0];
              const yOffset = hasBoth
                ? (marker.type === "buy" ? -1 : 1) * yRange * 0.02
                : 0;
              // Scale dot size by trade count (5 base, +1.5 per extra, max 10)
              const r = Math.min(5 + (marker.count - 1) * 1.5, 10);

              return (
                <ReferenceDot
                  key={`${marker.date}-${marker.type}-${idx}`}
                  x={marker.date}
                  y={point.value + yOffset}
                  r={r}
                  fill={
                    marker.type === "buy"
                      ? CHART_COLORS.buy
                      : CHART_COLORS.sell
                  }
                  stroke="#fff"
                  strokeWidth={2}
                  label={
                    marker.count > 1
                      ? {
                          value: `${marker.count}`,
                          position: "top",
                          fontSize: 9,
                          fontWeight: 600,
                          fill:
                            marker.type === "buy"
                              ? CHART_COLORS.buy
                              : CHART_COLORS.sell,
                        }
                      : undefined
                  }
                />
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      {/* Drawdown subchart (메인 차트와 같은 구간) */}
      {chartData.some((d) => d.drawdown < 0) && (
        <>
          <div className="flex items-center justify-between mt-4 mb-2">
            <h4 className="text-xs font-medium text-slate-500">Drawdown</h4>
          </div>
          <div className="h-[120px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart
                data={visibleData}
                margin={{ top: 0, right: RIGHT_MARGIN, left: 0, bottom: 0 }}
              >
                <defs>
                  <linearGradient
                    id="gradDrawdown"
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="1"
                  >
                    <stop
                      offset="5%"
                      stopColor={CHART_COLORS.drawdown}
                      stopOpacity={0.4}
                    />
                    <stop
                      offset="95%"
                      stopColor={CHART_COLORS.drawdown}
                      stopOpacity={0.05}
                    />
                  </linearGradient>
                </defs>
                <CartesianGrid
                  strokeDasharray="3 3"
                  stroke={CHART_COLORS.grid}
                  vertical={false}
                />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9 }}
                  tickFormatter={(v: string) => v.slice(5)}
                  interval={xInterval}
                />
                <YAxis
                  tick={{ fontSize: 9 }}
                  tickFormatter={formatDrawdownAxis}
                  domain={drawdownDomain}
                  width={50}
                />
                <Tooltip content={DrawdownTooltip} />
                <ReferenceLine y={0} stroke={CHART_COLORS.grid} />
                <Area
                  type="monotone"
                  dataKey="drawdown"
                  stroke={CHART_COLORS.drawdown}
                  fill="url(#gradDrawdown)"
                  strokeWidth={1.5}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  );
}
