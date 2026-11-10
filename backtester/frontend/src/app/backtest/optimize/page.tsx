"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Calendar,
  Info,
  Loader2,
  Percent,
  Play,
  SlidersHorizontal,
  Square,
  Target,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { getHyperoptSpace, getHyperoptStatus, getMinuteCoverage, listStrategies, runHyperopt } from "@/lib/api";
import { FileDropZone, MyStrategyChips } from "@/components/file";
import { useMyStrategies, type MyStrategy } from "@/lib/myStrategies";
import { StockInput } from "@/components/symbols";
import { HyperoptProgress, HyperoptResults, ParamRangeTable, type TrialEvent } from "@/components/optimize";
import {
  BAR_MINUTE_OPTIONS,
  DAY_MS,
  RESOLUTION_OPTIONS,
  countWeekdays,
  formatDuration,
  groupStrategies,
  toDateInput,
} from "@/lib/backtestOptions";
import {
  MIN_TRAIN_WEEKDAYS,
  MIN_VALID_WEEKDAYS,
  defaultRows,
  estimateHyperopt,
  rowError,
  rowGridSize,
  toChoices,
  type ParamRow,
} from "@/lib/hyperopt";
import { saveBacktestPrefill } from "@/lib/prefill";
import type {
  BacktestResolution,
  BarMinutes,
  HyperoptBest,
  HyperoptCandidate,
  HyperoptDoneEvent,
  HyperoptEvent,
  HyperoptObjective,
  HyperoptObjectiveInfo,
  HyperoptSpace,
  HyperoptStartEvent,
  MinuteCoverage,
  Strategy,
} from "@/types";

const DEFAULT_OBJECTIVES: HyperoptObjectiveInfo[] = [
  { id: "sharpe", label: "샤프 비율", description: "변동성 대비 수익. 가장 많이 쓰는 기준" },
  { id: "sortino", label: "소르티노 비율", description: "떨어질 때의 변동성만 위험으로 보는 샤프" },
  { id: "calmar", label: "칼마 비율", description: "연 수익률 ÷ 최대 낙폭" },
  { id: "return_dd", label: "수익률 ÷ 최대 낙폭", description: "총 수익률 ÷ 최대 낙폭" },
  { id: "return", label: "총 수익률", description: "위험은 보지 않고 수익률만" },
];

const VALIDATION_OPTIONS = [
  { value: 0, label: "없음" },
  { value: 0.2, label: "20%" },
  { value: 0.3, label: "30%" },
  { value: 0.4, label: "40%" },
  { value: 0.5, label: "50%" },
];

const TRIAL_PRESETS = [30, 100, 200];

// 마지막 결과를 브라우저 탭에 잠깐 보관 (백테스트 화면에 다녀와도 그대로 보이게)
const LAST_RUN_KEY = "limson.hyperopt.last";

interface FormState {
  selectedId: string | null;
  importedYaml: string | null;
  symbols: string[];
  startDate: string;
  endDate: string;
  initialCapital: number;
  resolution: BacktestResolution;
  barMinutes: BarMinutes;
  intradayExitOn: boolean;
  intradayExitTime: string;
  commissionRate: number;
  taxRate: number;
  slippage: number;
  objective: HyperoptObjective;
  nTrials: number;
  minTrades: number;
  validationRatio: number;
  concurrency: number;
  timeBudget: number;
  seed: string;
}

interface SavedRun {
  form: FormState;
  rows: Record<string, ParamRow>;
  start: HyperoptStartEvent;
  trials: TrialEvent[];
  done: HyperoptDoneEvent;
}

function loadSavedRun(): SavedRun | null {
  try {
    const raw = sessionStorage.getItem(LAST_RUN_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw) as SavedRun;
    return saved?.form && saved.start && saved.done ? saved : null;
  } catch {
    return null;
  }
}

function saveRun(run: SavedRun): void {
  try {
    sessionStorage.setItem(LAST_RUN_KEY, JSON.stringify(run));
  } catch {
    // 저장 공간이 모자라면(아주 긴 결과) 보관하지 않는다
  }
}

export default function HyperoptPage() {
  const router = useRouter();

  // 전략
  const [allStrategies, setAllStrategies] = useState<Strategy[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [backendDown, setBackendDown] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importedYaml, setImportedYaml] = useState<string | null>(null);

  // 탐색 공간 (전략을 고르면 서버에서 받아 온다)
  const [space, setSpace] = useState<HyperoptSpace | null>(null);
  const [spaceLoading, setSpaceLoading] = useState(false);
  const [spaceError, setSpaceError] = useState<string | null>(null);
  const [rows, setRows] = useState<Record<string, ParamRow>>({});
  const pendingRows = useRef<Record<string, ParamRow> | null>(null);

  // 종목·기간
  const [selectedStocks, setSelectedStocks] = useState<string[]>([]);
  const [startDate, setStartDate] = useState(() => {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 2);
    return toDateInput(d);
  });
  const [endDate, setEndDate] = useState(() => toDateInput(new Date()));
  const [initialCapital, setInitialCapital] = useState(100_000_000);
  const [resolution, setResolution] = useState<BacktestResolution>("daily");
  const [barMinutes, setBarMinutes] = useState<BarMinutes>(5);
  const [intradayExitOn, setIntradayExitOn] = useState(false);
  const [intradayExitTime, setIntradayExitTime] = useState("15:15");
  const [minuteCoverage, setMinuteCoverage] = useState<MinuteCoverage | null>(null);

  // 거래 비용 (%)
  const [commissionRate, setCommissionRate] = useState(0.015);
  const [taxRate, setTaxRate] = useState(0.2);
  const [slippage, setSlippage] = useState(0.1);

  // 하이퍼옵트 설정
  const [objective, setObjective] = useState<HyperoptObjective>("sharpe");
  const [nTrials, setNTrials] = useState(100);
  const [minTrades, setMinTrades] = useState(3);
  const [validationRatio, setValidationRatio] = useState(0.3);
  const [concurrency, setConcurrency] = useState(4);
  const [maxConcurrency, setMaxConcurrency] = useState(4);
  const [timeBudget, setTimeBudget] = useState(60);
  const [seed, setSeed] = useState("");

  // 실행 상태
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState<"data" | "search" | "validate" | null>(null);
  const [startEvent, setStartEvent] = useState<HyperoptStartEvent | null>(null);
  const [trials, setTrials] = useState<TrialEvent[]>([]);
  const [best, setBest] = useState<HyperoptBest | null>(null);
  const [validation, setValidation] = useState<{ done: number; total: number } | null>(null);
  const [retries, setRetries] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [done, setDone] = useState<HyperoptDoneEvent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [eta, setEta] = useState<{ value: number; at: number } | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [otherRunning, setOtherRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const startedAtRef = useRef(0);
  const formRef = useRef<FormState | null>(null);
  const rowsRef = useRef<Record<string, ParamRow>>({});

  const form: FormState = {
    selectedId, importedYaml, symbols: selectedStocks, startDate, endDate, initialCapital, resolution, barMinutes,
    intradayExitOn, intradayExitTime, commissionRate, taxRate, slippage, objective, nTrials, minTrades,
    validationRatio, concurrency, timeBudget, seed,
  };

  // ---- 처음 불러오기 -------------------------------------------------------
  useEffect(() => {
    listStrategies()
      .then((res) => {
        setAllStrategies(res.data || []);
        setBackendDown(false);
      })
      .catch(() => setBackendDown(true))
      .finally(() => setIsLoading(false));
    getMinuteCoverage().then(setMinuteCoverage).catch(() => setMinuteCoverage(null));
    getHyperoptStatus()
      .then((status) => {
        setOtherRunning(status.running);
        setMaxConcurrency(status.max_concurrency);
        setConcurrency((c) => Math.min(c, status.max_concurrency));
      })
      .catch(() => undefined);

    const saved = loadSavedRun();
    if (saved) {
      const f = saved.form;
      setSelectedId(f.selectedId);
      setImportedYaml(f.importedYaml);
      if (f.symbols.length) setSelectedStocks(f.symbols);
      setStartDate(f.startDate);
      setEndDate(f.endDate);
      setInitialCapital(f.initialCapital);
      setResolution(f.resolution);
      setBarMinutes(f.barMinutes);
      setIntradayExitOn(f.intradayExitOn);
      setIntradayExitTime(f.intradayExitTime);
      setCommissionRate(f.commissionRate);
      setTaxRate(f.taxRate);
      setSlippage(f.slippage);
      setObjective(f.objective);
      setNTrials(f.nTrials);
      setMinTrades(f.minTrades);
      setValidationRatio(f.validationRatio);
      setConcurrency(f.concurrency);
      setTimeBudget(f.timeBudget);
      setSeed(f.seed);
      pendingRows.current = saved.rows;
      formRef.current = saved.form;
      rowsRef.current = saved.rows;
      setStartEvent(saved.start);
      setTrials(saved.trials);
      setDone(saved.done);
      setPhase("validate");
      setElapsed(saved.done.elapsed);
      const bestTrial = saved.trials
        .filter((t) => t.kind === "trial" && t.state === "ok" && t.score !== null)
        .sort((a, b) => (b.score as number) - (a.score as number))[0];
      if (bestTrial) setBest({ number: bestTrial.number, score: bestTrial.score, params: bestTrial.params });
    }
  }, []);

  // 실행 중에는 페이지를 닫거나 새로고침하기 전에 묻는다. 페이지를 떠나면 멈춘다.
  useEffect(() => {
    if (!running) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
      clearInterval(timer);
    };
  }, [running]);
  useEffect(() => () => abortRef.current?.abort(), []);

  // ---- 전략 → 탐색 공간 ----------------------------------------------------
  useEffect(() => {
    if (!selectedId && !importedYaml) {
      setSpace(null);
      setRows({});
      return;
    }
    let cancelledFetch = false;
    setSpaceLoading(true);
    setSpaceError(null);
    getHyperoptSpace(importedYaml ? { yaml_content: importedYaml } : { strategy_id: selectedId! })
      .then((result) => {
        if (cancelledFetch) return;
        setSpace(result);
        const defaults = defaultRows(result);
        const pending = pendingRows.current;
        pendingRows.current = null;
        if (pending && Object.keys(pending).every((name) => name in defaults)) {
          setRows({ ...defaults, ...pending });
        } else {
          setRows(defaults);
        }
      })
      .catch((e) => {
        if (cancelledFetch) return;
        setSpace(null);
        setRows({});
        setSpaceError(e instanceof Error ? e.message : "전략의 파라미터를 불러오지 못했습니다.");
      })
      .finally(() => {
        if (!cancelledFetch) setSpaceLoading(false);
      });
    return () => {
      cancelledFetch = true;
    };
  }, [selectedId, importedYaml]);

  const handleRowChange = useCallback((name: string, patch: Partial<ParamRow>) => {
    setRows((prev) => ({ ...prev, [name]: { ...prev[name], ...patch } }));
  }, []);

  // ---- 기간·봉 ----------------------------------------------------------------
  const minuteLimits = useMemo(() => {
    const today = toDateInput(new Date());
    const latest = minuteCoverage?.latest && minuteCoverage.latest < today ? minuteCoverage.latest : today;
    return { earliest: minuteCoverage?.earliest ?? undefined, latest, today };
  }, [minuteCoverage]);

  // 분봉으로 바꿀 때 기간이 DB 범위를 벗어나 있으면 DB의 최근 3개월로 맞춘다 (학습·검증으로 나누려면 한 달은 짧다).
  const handleResolutionChange = useCallback((next: BacktestResolution) => {
    setResolution(next);
    if (next !== "minute") return;
    const { earliest, latest } = minuteLimits;
    if ((earliest && startDate < earliest) || endDate > latest) {
      const threeMonthsAgo = toDateInput(new Date(Date.parse(latest) - 91 * DAY_MS));
      setEndDate(latest);
      setStartDate(earliest && threeMonthsAgo < earliest ? earliest : threeMonthsAgo);
    }
  }, [startDate, endDate, minuteLimits]);

  const handleStrategySelect = useCallback((id: string | null) => {
    setSelectedId(id);
    setImportedYaml(null);
    const strategy = allStrategies.find((s) => s.id === id);
    if (strategy?.tags?.includes("intraday")) {
      handleResolutionChange("minute");
      setBarMinutes(5);
      setIntradayExitOn(true);
      setIntradayExitTime("15:15");
    }
  }, [allStrategies, handleResolutionChange]);

  const handleImport = useCallback((_file: File, content: string) => {
    setImportedYaml(content);
    setSelectedId(null);
  }, []);

  // 전략 빌더의 내 전략 (같은 브라우저 localStorage)
  const myStrategies = useMyStrategies();
  const handleMyStrategySelect = useCallback((strategy: MyStrategy | null) => {
    setImportedYaml(strategy?.yaml ?? null);
    setSelectedId(null);
  }, []);

  const strategyGroups = useMemo(() => groupStrategies(allStrategies), [allStrategies]);
  const strategyName = space?.name ?? allStrategies.find((s) => s.id === selectedId)?.name ?? null;

  // ---- 검사·예상 시간 -------------------------------------------------------------
  const effectiveEnd = resolution === "minute" && endDate > minuteLimits.latest ? minuteLimits.latest : endDate;
  const barsPerDay = minuteCoverage?.bars_per_day[String(barMinutes)] ?? 0;

  const estimate = useMemo(() => {
    if (!startDate || !effectiveEnd || startDate > effectiveEnd) return null;
    return estimateHyperopt({
      symbolCount: selectedStocks.length,
      startDate,
      endDate: effectiveEnd,
      validationRatio,
      resolution,
      barsPerDay,
      nTrials,
      concurrency,
      timeBudgetMinutes: timeBudget,
    });
  }, [startDate, effectiveEnd, selectedStocks.length, validationRatio, resolution, barsPerDay, nTrials, concurrency, timeBudget]);

  const searchCount = space ? space.params.filter((spec) => rows[spec.name]?.optimize).length : 0;
  const combinations = useMemo(() => {
    if (!space) return 0;
    return space.params.reduce((acc, spec) => {
      const row = rows[spec.name];
      return row?.optimize ? acc * Math.max(rowGridSize(row), 1) : acc;
    }, 1);
  }, [space, rows]);

  const formError = useMemo(() => {
    if (!selectedId && !importedYaml) return "전략을 고르거나 YAML 파일을 넣으세요.";
    if (spaceError) return spaceError;
    if (!space) return "전략의 파라미터를 불러오는 중입니다.";
    if (selectedStocks.length === 0) return "종목을 하나 이상 고르세요.";
    if (selectedStocks.length > 20) return "종목은 20개까지 고를 수 있습니다.";
    if (!startDate || !endDate || startDate > endDate) return "시작일은 종료일보다 늦을 수 없습니다.";
    for (const spec of space.params) {
      const row = rows[spec.name];
      if (row && rowError(spec, row)) return `${spec.label}: ${rowError(spec, row)}`;
    }
    if (searchCount === 0) return "찾을 파라미터를 하나 이상 체크하세요.";
    if (searchCount > space.max_search_params) return `파라미터는 한 번에 ${space.max_search_params}개까지 찾을 수 있습니다. 일부는 고정하세요.`;
    if (resolution === "minute") {
      if (endDate > minuteLimits.today) return "분봉 종료일은 오늘 이후일 수 없습니다.";
      if (minuteLimits.earliest && startDate < minuteLimits.earliest) return `DB 분봉은 ${minuteLimits.earliest}부터 있습니다.`;
      if (startDate > minuteLimits.latest) return `DB 분봉은 ${minuteLimits.latest}까지 있습니다.`;
      if (minuteCoverage) {
        const bars = selectedStocks.length * countWeekdays(startDate, effectiveEnd) * barsPerDay;
        if (bars > minuteCoverage.max_bars) {
          return `백테스트 한 번이 ${bars.toLocaleString()}봉으로 한도(${minuteCoverage.max_bars.toLocaleString()}봉)를 넘습니다. 봉 주기를 늘리거나 종목·기간을 줄이세요.`;
        }
      }
      if (intradayExitOn && !(intradayExitTime > "09:00" && intradayExitTime <= "15:30")) {
        return "장마감 청산 시각은 09:01 ~ 15:30 사이여야 합니다.";
      }
    }
    if (estimate && validationRatio > 0 && (estimate.trainWeekdays < MIN_TRAIN_WEEKDAYS || estimate.validWeekdays < MIN_VALID_WEEKDAYS)) {
      return `기간이 짧아 학습/검증으로 나눌 수 없습니다 (학습 ${estimate.trainWeekdays}일, 검증 ${estimate.validWeekdays}일). 기간을 늘리거나 검증을 "없음"으로 하세요.`;
    }
    if (!Number.isInteger(nTrials) || nTrials < 5 || nTrials > 500) return "시행 횟수는 5 ~ 500 사이여야 합니다.";
    if (!Number.isInteger(timeBudget) || timeBudget < 1 || timeBudget > 240) return "시간 제한은 1 ~ 240분 사이여야 합니다.";
    if (seed.trim() && !/^\d+$/.test(seed.trim())) return "시드는 0 이상의 정수로 넣거나 비워 두세요.";
    return null;
  }, [selectedId, importedYaml, spaceError, space, selectedStocks.length, startDate, endDate, rows, searchCount, resolution,
    minuteLimits, minuteCoverage, effectiveEnd, barsPerDay, intradayExitOn, intradayExitTime, estimate, validationRatio, nTrials,
    timeBudget, seed]);

  // ---- 실행 ---------------------------------------------------------------------
  const handleEvent = useCallback((event: HyperoptEvent) => {
    switch (event.type) {
      case "start":
        setStartEvent(event);
        if (event.warnings.length) setWarnings(event.warnings);
        break;
      case "phase":
        setPhase(event.phase);
        if (event.phase === "validate") setValidation({ done: 0, total: event.total ?? 0 });
        break;
      case "trial":
        setTrials((prev) => [...prev, event]);
        if (event.best) setBest(event.best);
        setElapsed(event.elapsed);
        setEta({ value: event.eta, at: Date.now() });
        break;
      case "retry":
        setRetries((r) => r + 1);
        break;
      case "validation":
        setValidation({ done: event.done, total: event.total });
        setElapsed(event.elapsed);
        break;
      case "warnings":
        setWarnings((prev) => [...prev, ...event.warnings]);
        break;
      case "done":
        setDone(event);
        setElapsed(event.elapsed);
        break;
      case "error":
        setError(event.message);
        break;
      case "cancelled":
        setCancelled(true);
        break;
      default:
        break;
    }
  }, []);

  // done 이벤트가 오면 결과를 탭에 보관
  useEffect(() => {
    if (!done || !startEvent || running) return;
    if (formRef.current) {
      saveRun({ form: formRef.current, rows: rowsRef.current, start: startEvent, trials, done });
    }
  }, [done, startEvent, trials, running]);

  const handleStart = useCallback(async () => {
    if (formError || !space) return;
    const controller = new AbortController();
    abortRef.current = controller;
    formRef.current = form;
    rowsRef.current = rows;
    try {
      sessionStorage.removeItem(LAST_RUN_KEY);
    } catch {
      // 무시
    }
    setRunning(true);
    setPhase(null);
    setStartEvent(null);
    setTrials([]);
    setBest(null);
    setValidation(null);
    setRetries(0);
    setWarnings([]);
    setDone(null);
    setError(null);
    setCancelled(false);
    setElapsed(0);
    setEta(null);
    startedAtRef.current = Date.now();
    setNow(Date.now());

    try {
      await runHyperopt(
        {
          ...(importedYaml ? { yaml_content: importedYaml } : { strategy_id: selectedId! }),
          symbols: selectedStocks,
          start_date: startDate,
          end_date: endDate,
          initial_capital: initialCapital,
          commission_rate: commissionRate / 100,
          tax_rate: taxRate / 100,
          slippage: slippage / 100,
          resolution,
          ...(resolution === "minute"
            ? { bar_minutes: barMinutes, intraday_exit: intradayExitOn ? intradayExitTime : null }
            : {}),
          params: toChoices(space, rows),
          objective,
          n_trials: nTrials,
          min_trades: minTrades,
          validation_ratio: validationRatio,
          concurrency,
          time_budget_minutes: timeBudget,
          seed: seed.trim() ? Number(seed.trim()) : null,
        },
        handleEvent,
        controller.signal
      );
    } catch (e) {
      if (controller.signal.aborted) setCancelled(true);
      else setError(e instanceof Error ? e.message : "하이퍼옵트 실행 중 오류");
    } finally {
      setRunning(false);
      abortRef.current = null;
      getHyperoptStatus().then((s) => setOtherRunning(s.running)).catch(() => undefined);
    }
    // form·rows는 시작 순간 값을 쓴다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formError, space, importedYaml, selectedId, selectedStocks, startDate, endDate, initialCapital, commissionRate, taxRate,
    slippage, resolution, barMinutes, intradayExitOn, intradayExitTime, rows, objective, nTrials, minTrades, validationRatio,
    concurrency, timeBudget, seed, handleEvent]);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  // ---- 결과 → 백테스트 ------------------------------------------------------------
  const handleBacktest = useCallback((candidate: HyperoptCandidate) => {
    const f = formRef.current ?? form;
    saveBacktestPrefill({
      strategyId: f.importedYaml ? null : f.selectedId,
      yaml: f.importedYaml ? candidate.yaml : null,
      symbols: f.symbols,
      startDate: f.startDate,
      endDate: f.endDate,
      resolution: f.resolution,
      barMinutes: f.barMinutes,
      intradayExit: f.resolution === "minute" && f.intradayExitOn ? f.intradayExitTime : null,
      initialCapital: f.initialCapital,
      commissionRate: f.commissionRate,
      taxRate: f.taxRate,
      slippage: f.slippage,
      params: f.importedYaml ? undefined : candidate.full_params,
      note: `하이퍼옵트 ${candidate.label} 값을 불러왔습니다. 기간은 학습+검증 전체(${f.startDate} ~ ${f.endDate})입니다.`,
    });
    router.push("/backtest");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [router]);

  const handleDownloadYaml = useCallback((candidate: HyperoptCandidate) => {
    if (!candidate.yaml) return;
    const blob = new Blob([candidate.yaml], { type: "application/x-yaml" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${startEvent?.strategy.id ?? "strategy"}_hyperopt_${candidate.rank ? `${candidate.rank}위` : "기본값"}.kis.yaml`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }, [startEvent]);

  const shownElapsed = running && startedAtRef.current ? Math.max(elapsed, (now - startedAtRef.current) / 1000) : elapsed;
  const shownEta = running && eta ? Math.max(0, eta.value - (now - eta.at) / 1000) : null;
  const objectives = space?.objectives ?? DEFAULT_OBJECTIVES;
  const objectiveInfo = objectives.find((o) => o.id === objective);
  const showProgress = running || trials.length > 0 || phase !== null;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8 space-y-6">
      {backendDown && (
        <div className="p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-xl flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
          <p className="text-sm text-red-700 dark:text-red-400">백테스트 서버에 연결할 수 없습니다. 로컬 PC 워커가 켜져 있는지 확인하세요.</p>
        </div>
      )}

      <div>
        <h1 className="text-2xl font-bold text-slate-900 dark:text-white flex items-center gap-2">
          <SlidersHorizontal className="w-6 h-6 text-kis-blue" />
          하이퍼옵트
        </h1>
        <p className="text-slate-600 dark:text-slate-400 mt-1">
          전략의 숫자(이동평균 기간, 손절 % 등)를 자동으로 바꿔 가며 백테스트를 반복해, 고른 기준 점수가 가장 좋은 조합을 찾습니다.
        </p>
      </div>

      {/* 설정 */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4 items-start">
        <div className="space-y-4">
          <FileDropZone onFileSelect={handleImport} className="min-h-[110px]" />
          <div className="card">
            <h3 className="font-semibold text-slate-900 dark:text-white mb-3 flex items-center gap-2">
              <Zap className="w-4 h-4 text-kis-blue" />
              전략 선택
            </h3>
            {isLoading ? (
              <Loader2 className="w-5 h-5 animate-spin text-slate-400 mx-auto" />
            ) : (
              <div className="space-y-2.5">
                {strategyGroups.map((group) => (
                  <div key={group.category}>
                    <p className="text-[11px] font-medium text-slate-400 mb-1">{group.label}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {group.strategies.map((t) => {
                        const selected = t.id === selectedId && !importedYaml;
                        return (
                          <button
                            key={t.id}
                            type="button"
                            disabled={running}
                            onClick={() => handleStrategySelect(selected ? null : t.id)}
                            title={t.description}
                            aria-pressed={selected}
                            className={cn(
                              "px-2.5 py-1 rounded-full border text-xs transition-colors disabled:opacity-60",
                              selected
                                ? "bg-kis-blue border-kis-blue text-white"
                                : "border-slate-200 dark:border-slate-700 text-slate-700 dark:text-slate-300 hover:border-kis-blue hover:text-kis-blue"
                            )}
                          >
                            {t.name}
                            {t.tags?.includes("intraday") && (
                              <span className={cn("ml-1 text-[10px]", selected ? "text-white/80" : "text-slate-400")}>· 분봉</span>
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))}
                <MyStrategyChips
                  strategies={myStrategies.strategies}
                  loaded={myStrategies.loaded}
                  selectedYaml={importedYaml}
                  onSelect={handleMyStrategySelect}
                  disabled={running}
                />
              </div>
            )}
            {importedYaml && (
              <div className="mt-2 px-2 py-1 bg-amber-100 text-amber-700 rounded text-xs">
                {myStrategies.strategies.some((s) => s.yaml === importedYaml) ? "내 전략 사용 중" : "YAML 전략 사용 중"}
                {strategyName ? ` · ${strategyName}` : ""}
              </div>
            )}
          </div>
        </div>

        <div className="card">
          <h3 className="font-semibold text-slate-900 dark:text-white mb-3 flex items-center gap-2">
            <Target className="w-4 h-4 text-kis-blue" />
            종목 선택
          </h3>
          <StockInput stocks={selectedStocks} onChange={setSelectedStocks} />
        </div>

        <div className="card">
          <h3 className="font-semibold text-slate-900 dark:text-white mb-3 flex items-center gap-2">
            <Calendar className="w-4 h-4 text-kis-blue" />
            기간 설정
          </h3>
          <div className="space-y-3">
            <div role="radiogroup" aria-label="봉 단위" className="grid grid-cols-2 gap-1 p-1 rounded-lg bg-slate-100 dark:bg-slate-800">
              {RESOLUTION_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={resolution === option.value}
                  disabled={running}
                  onClick={() => handleResolutionChange(option.value)}
                  className={cn(
                    "py-1.5 rounded-md text-sm font-medium transition-colors",
                    resolution === option.value
                      ? "bg-white dark:bg-slate-700 text-kis-blue shadow-sm"
                      : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
            {resolution === "minute" && (
              <div role="radiogroup" aria-label="봉 주기" className="grid grid-cols-7 gap-1 p-1 rounded-lg bg-slate-100 dark:bg-slate-800">
                {BAR_MINUTE_OPTIONS.map((minutes) => (
                  <button
                    key={minutes}
                    type="button"
                    role="radio"
                    aria-checked={barMinutes === minutes}
                    disabled={running}
                    onClick={() => setBarMinutes(minutes)}
                    className={cn(
                      "py-1.5 rounded-md text-xs font-medium transition-colors",
                      barMinutes === minutes
                        ? "bg-white dark:bg-slate-700 text-kis-blue shadow-sm"
                        : "text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                    )}
                  >
                    {minutes}분
                  </button>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-xs text-slate-500 mb-1 block">시작일</label>
                <input
                  type="date"
                  value={startDate}
                  disabled={running}
                  min={resolution === "minute" ? minuteLimits.earliest : undefined}
                  max={resolution === "minute" ? minuteLimits.latest : undefined}
                  onChange={(e) => setStartDate(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
                />
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">종료일</label>
                <input
                  type="date"
                  value={endDate}
                  disabled={running}
                  min={resolution === "minute" ? minuteLimits.earliest : undefined}
                  max={resolution === "minute" ? minuteLimits.today : undefined}
                  onChange={(e) => setEndDate(e.target.value)}
                  className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
                />
              </div>
            </div>
            <p className="text-xs text-slate-400">
              {resolution === "minute"
                ? minuteCoverage?.earliest
                  ? `PostgreSQL 분봉 · ${minuteCoverage.earliest} ~ ${minuteCoverage.latest}`
                  : "분봉 데이터 범위 확인 중..."
                : "PostgreSQL 일봉 (KIS 수정주가)"}
            </p>
            {resolution === "minute" && (
              <div className="flex items-center justify-between gap-2">
                <label className="flex items-center gap-2 text-xs text-slate-500">
                  <input type="checkbox" checked={intradayExitOn} disabled={running} onChange={(e) => setIntradayExitOn(e.target.checked)} />
                  장마감 청산 (당일 매매)
                </label>
                <input
                  type="time"
                  value={intradayExitTime}
                  min="09:01"
                  max="15:30"
                  disabled={!intradayExitOn || running}
                  onChange={(e) => setIntradayExitTime(e.target.value)}
                  aria-label="장마감 청산 시각"
                  className="px-2 py-1 border border-slate-200 dark:border-slate-700 rounded-lg text-sm disabled:opacity-50"
                />
              </div>
            )}
            <div>
              <label className="text-xs text-slate-500 mb-1 block">초기 자본 (원)</label>
              <input
                type="number"
                value={initialCapital}
                disabled={running}
                onChange={(e) => setInitialCapital(Number(e.target.value))}
                className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
              />
            </div>
          </div>
        </div>

        <div className="card">
          <h3 className="font-semibold text-slate-900 dark:text-white mb-3 flex items-center gap-2">
            <Percent className="w-4 h-4 text-kis-blue" />
            거래 비용
          </h3>
          <div className="grid grid-cols-3 gap-2">
            {[
              { label: "수수료 %", value: commissionRate, set: setCommissionRate, step: "0.001" },
              { label: "거래세 %", value: taxRate, set: setTaxRate, step: "0.01" },
              { label: "슬리피지 %", value: slippage, set: setSlippage, step: "0.01" },
            ].map((item) => (
              <div key={item.label}>
                <label className="text-xs text-slate-500 mb-1 block">{item.label}</label>
                <input
                  type="number"
                  step={item.step}
                  value={item.value}
                  disabled={running}
                  onChange={(e) => item.set(Number(e.target.value))}
                  className="w-full px-2 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
                />
              </div>
            ))}
          </div>
          <p className="text-xs text-slate-400 mt-2">백테스트 화면과 같은 비용으로 계산합니다.</p>
        </div>
      </div>

      {/* 탐색 범위 + 하이퍼옵트 설정 */}
      <div className="grid gap-4 lg:grid-cols-3 items-start">
        <div className="card lg:col-span-2">
          <h3 className="font-semibold text-slate-900 dark:text-white mb-1 flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-kis-blue" />
            찾을 파라미터와 범위
            {strategyName && <span className="text-xs font-normal text-slate-500">· {strategyName}</span>}
          </h3>
          <p className="text-xs text-slate-500 mb-3">
            체크한 숫자만 범위 안에서 바꿔 보고, 체크를 끈 숫자는 적힌 값으로 고정합니다. 간격이 넓을수록 빨리 훑습니다.
          </p>
          {spaceLoading ? (
            <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
          ) : space ? (
            <>
              <ParamRangeTable space={space} rows={rows} onChange={handleRowChange} disabled={running} />
              <div className="mt-3 text-xs text-slate-500 space-y-1">
                {space.constraints.map((c) => (
                  <p key={`${c.smaller}-${c.larger}`}>조건: {c.label} — 어기는 조합은 시험하지 않습니다.</p>
                ))}
                {searchCount > 0 && (
                  <p>
                    {searchCount}개 파라미터 · 가능한 조합 약 {combinations.toLocaleString()}개 중 {nTrials}개를 골라 시험합니다
                    {combinations > nTrials && " (점수가 좋은 쪽을 골라 가며 찾기 때문에 전부 돌릴 필요가 없습니다)"}.
                  </p>
                )}
              </div>
            </>
          ) : (
            <p className="text-sm text-slate-500">{spaceError ?? "위에서 전략을 고르면 바꿔 볼 수 있는 숫자가 여기 나옵니다."}</p>
          )}
        </div>

        <div className="card space-y-3">
          <h3 className="font-semibold text-slate-900 dark:text-white">하이퍼옵트 설정</h3>
          <div>
            <label className="text-xs text-slate-500 mb-1 block" htmlFor="objective">좋은 전략의 기준</label>
            <select
              id="objective"
              value={objective}
              disabled={running}
              onChange={(e) => setObjective(e.target.value as HyperoptObjective)}
              className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm bg-white dark:bg-slate-900"
            >
              {objectives.map((o) => (
                <option key={o.id} value={o.id}>{o.label}</option>
              ))}
            </select>
            {objectiveInfo && <p className="text-xs text-slate-400 mt-1">{objectiveInfo.description}</p>}
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1 block" htmlFor="n-trials">시행 횟수 (백테스트를 몇 번 돌릴지)</label>
            <div className="flex gap-1.5">
              <input
                id="n-trials"
                type="number"
                min={5}
                max={500}
                value={nTrials}
                disabled={running}
                onChange={(e) => setNTrials(Math.round(Number(e.target.value)))}
                className="w-24 px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
              />
              {TRIAL_PRESETS.map((preset) => (
                <button
                  key={preset}
                  type="button"
                  disabled={running}
                  onClick={() => setNTrials(preset)}
                  className={cn(
                    "px-2.5 rounded-lg border text-xs",
                    nTrials === preset ? "border-kis-blue text-kis-blue" : "border-slate-200 dark:border-slate-700 text-slate-500"
                  )}
                >
                  {preset}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-slate-500 mb-1 block" htmlFor="min-trades">최소 매수 횟수</label>
              <input
                id="min-trades"
                type="number"
                min={0}
                value={minTrades}
                disabled={running}
                onChange={(e) => setMinTrades(Math.max(0, Math.round(Number(e.target.value))))}
                className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
              />
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block" htmlFor="time-budget">시간 제한 (분)</label>
              <input
                id="time-budget"
                type="number"
                min={1}
                max={240}
                value={timeBudget}
                disabled={running}
                onChange={(e) => setTimeBudget(Math.round(Number(e.target.value)))}
                className="w-full px-3 py-2 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
              />
            </div>
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1 block">검증 기간 (뒤쪽 기간을 떼어 놓고 마지막에 확인)</label>
            <div role="radiogroup" aria-label="검증 비율" className="grid grid-cols-5 gap-1 p-1 rounded-lg bg-slate-100 dark:bg-slate-800">
              {VALIDATION_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={validationRatio === option.value}
                  disabled={running}
                  onClick={() => setValidationRatio(option.value)}
                  className={cn(
                    "py-1.5 rounded-md text-xs font-medium transition-colors",
                    validationRatio === option.value ? "bg-white dark:bg-slate-700 text-kis-blue shadow-sm" : "text-slate-500"
                  )}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs text-slate-500 mb-1 block">동시 실행</label>
              <div role="radiogroup" aria-label="동시 실행 개수" className="grid grid-cols-4 gap-1 p-1 rounded-lg bg-slate-100 dark:bg-slate-800">
                {Array.from({ length: Math.max(1, Math.min(maxConcurrency, 4)) }, (_, i) => i + 1).map((n) => (
                  <button
                    key={n}
                    type="button"
                    role="radio"
                    aria-checked={concurrency === n}
                    disabled={running}
                    onClick={() => setConcurrency(n)}
                    className={cn(
                      "py-1 rounded-md text-xs font-medium",
                      concurrency === n ? "bg-white dark:bg-slate-700 text-kis-blue shadow-sm" : "text-slate-500"
                    )}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <label className="text-xs text-slate-500 mb-1 block" htmlFor="seed">시드 (같은 결과 재현)</label>
              <input
                id="seed"
                type="text"
                inputMode="numeric"
                placeholder="비우면 무작위"
                value={seed}
                disabled={running}
                onChange={(e) => setSeed(e.target.value)}
                className="w-full px-3 py-1.5 border border-slate-200 dark:border-slate-700 rounded-lg text-sm"
              />
            </div>
          </div>

          {estimate && !formError && (
            <p className="text-xs text-slate-500 flex items-start gap-1.5">
              <Info className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              예상 {formatDuration(estimate.total)} (백테스트 1회 {formatDuration(estimate.perRun)} × {nTrials + 1}회, 동시 {concurrency}개
              {validationRatio > 0 && ", 검증 포함"}). 실행 중에는 이 PC가 바빠집니다.
            </p>
          )}
          {formError && !running && (
            <p role="alert" className="text-xs text-amber-600 dark:text-amber-400">{formError}</p>
          )}
          {otherRunning && !running && (
            <p className="text-xs text-amber-600">다른 하이퍼옵트가 실행 중입니다. 끝난 뒤에 시작할 수 있습니다.</p>
          )}

          {running ? (
            <button
              type="button"
              onClick={handleStop}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl font-bold bg-slate-800 hover:bg-slate-700 text-white"
            >
              <Square className="w-4 h-4" />
              멈추기
            </button>
          ) : (
            <button
              type="button"
              onClick={handleStart}
              disabled={!!formError}
              className={cn(
                "w-full flex items-center justify-center gap-2 py-3 rounded-xl font-bold transition-all",
                formError ? "bg-slate-200 text-slate-400 cursor-not-allowed" : "bg-kis-blue hover:bg-kis-blue-dark text-white shadow-lg"
              )}
            >
              <Play className="w-5 h-5" />
              하이퍼옵트 시작
            </button>
          )}
          {running && <p className="text-xs text-slate-400 text-center">이 페이지를 떠나면 하이퍼옵트도 멈춥니다.</p>}
        </div>
      </div>

      {/* 진행·결과 */}
      {error && (
        <div className="card bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-500 flex-shrink-0 mt-0.5" />
          <p className="text-red-600 dark:text-red-400 text-sm font-medium">{error}</p>
        </div>
      )}
      {cancelled && !error && (
        <div className="card text-sm text-slate-600 dark:text-slate-300">하이퍼옵트를 멈췄습니다. 돌던 백테스트도 정리했습니다.</div>
      )}
      {warnings.length > 0 && (
        <div className="p-3 rounded-lg border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-900/20 text-xs text-amber-700 dark:text-amber-300">
          <p className="font-medium mb-1">데이터 확인 필요</p>
          <ul className="list-disc ml-4 space-y-0.5">
            {warnings.map((w, i) => <li key={i}>{w}</li>)}
          </ul>
        </div>
      )}
      {showProgress && (
        <HyperoptProgress
          start={startEvent}
          phase={phase}
          running={running}
          trials={trials}
          best={best}
          elapsed={shownElapsed}
          eta={shownEta}
          validation={validation}
          retries={retries}
          finished={!running && done !== null}
        />
      )}
      {done && startEvent && (
        <HyperoptResults
          start={startEvent}
          done={done}
          trials={trials}
          onBacktest={handleBacktest}
          onDownloadYaml={handleDownloadYaml}
        />
      )}
      {!showProgress && !done && (
        <div className="card text-sm text-slate-500 leading-relaxed">
          <p className="font-medium text-slate-700 dark:text-slate-300 mb-1">어떻게 찾나요?</p>
          <p>
            처음 몇십 번은 범위 안에서 무작위로 값을 골라 백테스트하고, 그다음부터는 점수가 좋았던 값 근처를 더 많이 시험합니다
            (Optuna TPE). 기간의 뒤쪽 일부는 떼어 두었다가 마지막에 상위 조합을 다시 돌려, 과거에만 딱 맞춘 값인지 확인합니다.
          </p>
          <p className="mt-1 text-xs text-slate-400">
            {resolution === "minute" ? "분봉은 한 번에 시간이 더 걸리니 시행 횟수를 줄이거나 봉 주기를 늘리세요." : "일봉 100회는 보통 몇 분이면 끝납니다."}
          </p>
        </div>
      )}
    </div>
  );
}
