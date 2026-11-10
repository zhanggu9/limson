/**
 * Strategy Builder State Management Hook
 */

import { useReducer, useCallback, useMemo } from "react";
import type {
  BuilderState,
  BuilderAction,
  BuilderIndicator,
  BuilderCondition,
  BuilderMetadata,
  RiskManagement,
  YamlStrategy,
} from "@/types/builder";
import { getIndicatorById, CANDLESTICK_PATTERNS } from "@/lib/builder/constants";
import { randomToken } from "@/lib/utils";
import { builderStateToYaml, builderStateToYamlString } from "@/lib/builder/yaml";

// 캔들스틱 패턴 ID Set (빠른 lookup)
const CANDLESTICK_IDS = new Set(CANDLESTICK_PATTERNS.map(p => p.id));

// ── 지표 유형별 분류 Set (모듈 레벨 상수 — 매 호출마다 재생성 방지) ──
const MA_OVERLAY = new Set([
  "sma", "ema", "dema", "tema", "hma", "kama", "wma", "vidya",
  "alma", "lwma", "trima", "t3", "zlema", "frama",
]);
const PRICE_OVERLAY = new Set([
  "sar", "supertrend", "vwap", "vwma",
  "maximum", "minimum", "midpoint", "midprice", "regression",
]);
const RANGE_0_100 = new Set([
  "rsi", "mfi", "ultosc", "schaff",
]);
const STOCH_LIKE = new Set(["stochastic", "stochrsi"]);
const SIGNAL_CROSS = new Set(["macd", "ppo", "tsi", "kvo", "kst"]);
const BAND = new Set(["bollinger", "keltner", "donchian", "accbands"]);
const RANGE_WIDE = new Set(["cci", "cmo"]);
const RANGE_NEG = new Set(["williams_r"]);
const DIRECTIONAL = new Set(["aroon", "vortex"]);
const TREND_STRENGTH = new Set(["adx", "adxr", "chop", "mass_index"]);
const ZERO_CROSS = new Set([
  "momentum", "roc", "apo", "ao", "cho", "trix", "dpo",
  "coppock", "fisher", "eom", "rvi", "bop", "augen",
  "change", "logr", "cmf", "force", "returns", "alpha",
]);
const FILTER_THRESHOLD = new Set([
  "natr", "std", "variance", "volatility_ind", "beta",
]);
const RANGE_0_1 = new Set(["ibs"]);
const DISPARITY = new Set(["disparity"]);
/** ATR은 가격 수준에 비례하므로 고정 임계값 조건이 무의미 — 자동 생성 skip */
const SKIP_AUTO = new Set(["atr"]);

// ============================================================
// Auto-Condition Generation Logic
// ============================================================

/**
 * 지표 추가 시 지능적인 기본 진입/청산 조건을 자동 생성합니다.
 */
function generateAutoConditions(
  indicators: BuilderIndicator[]
): { entry: BuilderCondition[]; exit: BuilderCondition[] } {
  const entry: BuilderCondition[] = [];
  const exit: BuilderCondition[] = [];

  // 일반 지표와 캔들스틱 분리
  const regularIndicators = indicators.filter(ind => !CANDLESTICK_IDS.has(ind.indicatorId));
  const candlestickIndicators = indicators.filter(ind => CANDLESTICK_IDS.has(ind.indicatorId));

  // 같은 타입의 이동평균 찾기 (crossover 조건)
  const maIndicators: Record<string, BuilderIndicator[]> = {};
  regularIndicators.forEach(ind => {
    if (MA_OVERLAY.has(ind.indicatorId)) {
      if (!maIndicators[ind.indicatorId]) maIndicators[ind.indicatorId] = [];
      maIndicators[ind.indicatorId].push(ind);
    }
  });

  // MA 교차 조건 생성 (같은 타입 2개 이상이면)
  Object.values(maIndicators).forEach(mas => {
    if (mas.length >= 2) {
      const sorted = [...mas].sort((a, b) => {
        const ap = Number(a.params.period) || 20;
        const bp = Number(b.params.period) || 20;
        return ap - bp;
      });
      const fast = sorted[0];
      const slow = sorted[sorted.length - 1];
      entry.push({
        id: `auto_entry_${Date.now()}_ma`,
        left: { type: "indicator", indicatorAlias: fast.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "indicator", indicatorAlias: slow.alias, indicatorOutput: "value" },
      });
      exit.push({
        id: `auto_exit_${Date.now()}_ma`,
        left: { type: "indicator", indicatorAlias: fast.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "indicator", indicatorAlias: slow.alias, indicatorOutput: "value" },
      });
    }
  });

  // 일목균형표 전용
  const ichimokuInds = regularIndicators.filter(ind => ind.indicatorId === "ichimoku");
  ichimokuInds.forEach(ind => {
    const ts = `${Date.now()}_${randomToken(4)}`;
    entry.push({
      id: `auto_entry_${ts}_ichi`,
      left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "tenkan" },
      operator: "cross_above",
      right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "kijun" },
    });
    exit.push({
      id: `auto_exit_${ts}_ichi`,
      left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "tenkan" },
      operator: "cross_below",
      right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "kijun" },
    });
  });

  regularIndicators.forEach(ind => {
    const ts = `${Date.now()}_${randomToken(4)}`;
    const id = ind.indicatorId;

    if (id === "ichimoku") return;
    if (SKIP_AUTO.has(id)) return; // 자동 조건 생성 불가 지표 (사용자가 직접 설정)

    // ── 1) 이동평균 / 오버레이 → 가격 교차 ──
    if (MA_OVERLAY.has(id)) {
      const sameType = maIndicators[id] || [];
      if (sameType.length <= 1) {
        entry.push({
          id: `auto_entry_${ts}_ma`,
          left: { type: "price", priceField: "close" },
          operator: "cross_above",
          right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        });
        exit.push({
          id: `auto_exit_${ts}_ma`,
          left: { type: "price", priceField: "close" },
          operator: "cross_below",
          right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        });
      }
      return;
    }

    // ── 2) 가격 오버레이 → 가격 교차 ──
    if (PRICE_OVERLAY.has(id)) {
      const out = "value";
      entry.push({
        id: `auto_entry_${ts}_overlay`,
        left: { type: "price", priceField: "close" },
        operator: "cross_above",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: out },
      });
      exit.push({
        id: `auto_exit_${ts}_overlay`,
        left: { type: "price", priceField: "close" },
        operator: "cross_below",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: out },
      });
      return;
    }

    // ── 3) 0~100 범위 오실레이터 → 과매도/과매수 ──
    if (RANGE_0_100.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_rng100`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "value", value: 30 },
      });
      exit.push({
        id: `auto_exit_${ts}_rng100`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "value", value: 70 },
      });
      return;
    }

    // ── 4) 스토캐스틱류 → %K 기준 과매도/과매수 ──
    if (STOCH_LIKE.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_stoch`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "k" },
        operator: "cross_above",
        right: { type: "value", value: 20 },
      });
      exit.push({
        id: `auto_exit_${ts}_stoch`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "k" },
        operator: "cross_below",
        right: { type: "value", value: 80 },
      });
      return;
    }

    // ── 5) 시그널라인 교차 (MACD류) → value↔signal ──
    if (SIGNAL_CROSS.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_sigx`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "signal" },
      });
      exit.push({
        id: `auto_exit_${ts}_sigx`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "signal" },
      });
      return;
    }

    // ── 6) 밴드 → 가격 vs 상하단 ──
    if (BAND.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_band`,
        left: { type: "price", priceField: "close" },
        operator: "cross_above",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "lower" },
      });
      exit.push({
        id: `auto_exit_${ts}_band`,
        left: { type: "price", priceField: "close" },
        operator: "cross_below",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "upper" },
      });
      return;
    }

    // ── 7) ±100 범위 (CCI, CMO) → -100/+100 교차 ──
    if (RANGE_WIDE.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_wide`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "value", value: -100 },
      });
      exit.push({
        id: `auto_exit_${ts}_wide`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "value", value: 100 },
      });
      return;
    }

    // ── 8) Williams %R → -80/-20 교차 ──
    if (RANGE_NEG.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_neg`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "value", value: -80 },
      });
      exit.push({
        id: `auto_exit_${ts}_neg`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "value", value: -20 },
      });
      return;
    }

    // ── 9) 방향성 (Aroon, Vortex) → plus↔minus 교차 ──
    if (DIRECTIONAL.has(id)) {
      const plus = id === "aroon" ? "aroon_up" : "plus_vi";
      const minus = id === "aroon" ? "aroon_down" : "minus_vi";
      entry.push({
        id: `auto_entry_${ts}_dir`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: plus },
        operator: "cross_above",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: minus },
      });
      exit.push({
        id: `auto_exit_${ts}_dir`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: plus },
        operator: "cross_below",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: minus },
      });
      return;
    }

    // ── 10) 추세 강도 → 임계값 초과/하회 ──
    if (TREND_STRENGTH.has(id)) {
      const entryVal = id === "chop" ? 38.2 : (id === "mass_index" ? 27 : 25);
      const exitVal = id === "chop" ? 61.8 : (id === "mass_index" ? 26.5 : 20);
      const entryOp = id === "chop" ? "cross_below" as const : "cross_above" as const;
      const exitOp = id === "chop" ? "cross_above" as const : "cross_below" as const;
      entry.push({
        id: `auto_entry_${ts}_trend`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: entryOp,
        right: { type: "value", value: entryVal },
      });
      exit.push({
        id: `auto_exit_${ts}_trend`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: exitOp,
        right: { type: "value", value: exitVal },
      });
      return;
    }

    // ── 11) 제로라인 교차 → 0 상향/하향 돌파 ──
    if (ZERO_CROSS.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_zero`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "value", value: 0 },
      });
      exit.push({
        id: `auto_exit_${ts}_zero`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "value", value: 0 },
      });
      return;
    }

    // ── 12) 필터/통계 → 임계값 기반 ──
    if (FILTER_THRESHOLD.has(id)) {
      let entryVal = 0;
      let exitVal = 0;
      switch (id) {
        case "natr": entryVal = 2.0; exitVal = 1.0; break;
        case "beta": entryVal = 1.0; exitVal = 0.8; break;
        case "volatility_ind": entryVal = 0.02; exitVal = 0.01; break;
        case "std": entryVal = 1.0; exitVal = 0.5; break;
        case "variance": entryVal = 1.0; exitVal = 0.5; break;
      }
      entry.push({
        id: `auto_entry_${ts}_filter`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "greater_than",
        right: { type: "value", value: entryVal },
      });
      exit.push({
        id: `auto_exit_${ts}_filter`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "less_than",
        right: { type: "value", value: exitVal },
      });
      return;
    }

    // ── 13) IBS (0~1 범위) → 과매도/과매수 ──
    if (RANGE_0_1.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_r01`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "value", value: 0.2 },
      });
      exit.push({
        id: `auto_exit_${ts}_r01`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "value", value: 0.8 },
      });
      return;
    }

    // ── 14) 이격도 → (현재가/N일이평)×100. 낮을수록 저평가 → 매수 신호 ──
    if (DISPARITY.has(id)) {
      entry.push({
        id: `auto_entry_${ts}_disp`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_below",
        right: { type: "value", value: 95 },
      });
      exit.push({
        id: `auto_exit_${ts}_disp`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "cross_above",
        right: { type: "value", value: 105 },
      });
      return;
    }

    // ── 15) 피봇 포인트 → high/low 출력 사용 ──
    if (id === "pivot") {
      entry.push({
        id: `auto_entry_${ts}_pivot`,
        left: { type: "price", priceField: "close" },
        operator: "cross_above",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "high" },
      });
      exit.push({
        id: `auto_exit_${ts}_pivot`,
        left: { type: "price", priceField: "close" },
        operator: "cross_below",
        right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "low" },
      });
      return;
    }

    // ── 16) 연속 일수 → 임계값 ──
    if (id === "consecutive") {
      entry.push({
        id: `auto_entry_${ts}_cons`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "greater_than",
        right: { type: "value", value: 3 },
      });
      exit.push({
        id: `auto_exit_${ts}_cons`,
        left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
        operator: "less_than",
        right: { type: "value", value: -3 },
      });
      return;
    }

    // ── 17) 나머지 → 가격 교차 기본값 ──
    // obv, ad, adl
    entry.push({
      id: `auto_entry_${ts}_generic`,
      left: { type: "price", priceField: "close" },
      operator: "cross_above",
      right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
    });
    exit.push({
      id: `auto_exit_${ts}_generic`,
      left: { type: "price", priceField: "close" },
      operator: "cross_below",
      right: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
    });
  });

  // 캔들스틱 조건 생성
  candlestickIndicators.forEach(ind => {
    const ts = `${Date.now()}_${randomToken(4)}`;
    entry.push({
      id: `auto_entry_${ts}_candle`,
      isCandlestick: true,
      candlestickAlias: ind.alias,
      candlestickSignal: "bullish",
      left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
      operator: "greater_than",
      right: { type: "value", value: 0 },
    });
    exit.push({
      id: `auto_exit_${ts}_candle`,
      isCandlestick: true,
      candlestickAlias: ind.alias,
      candlestickSignal: "bearish",
      left: { type: "indicator", indicatorAlias: ind.alias, indicatorOutput: "value" },
      operator: "greater_than",
      right: { type: "value", value: 0 },
    });
  });

  return { entry, exit };
}

// ============================================================
// Initial State
// ============================================================

export const INITIAL_STATE: BuilderState = {
  metadata: {
    id: "",
    name: "custom_strategy",
    description: "직접 만든 전략입니다",
    category: "custom",
    tags: [],
    author: "user",
  },
  indicators: [],
  entry: {
    logic: "AND",
    conditions: [],
  },
  exit: {
    logic: "AND",
    conditions: [],
  },
  risk: {
    stopLoss: { enabled: false, percent: 5 },
    takeProfit: { enabled: false, percent: 10 },
    trailingStop: { enabled: false, percent: 3 },
  },
};

// ============================================================
// Reducer
// ============================================================

function builderReducer(state: BuilderState, action: BuilderAction): BuilderState {
  switch (action.type) {
    case "SET_METADATA":
      return {
        ...state,
        metadata: { ...state.metadata, ...action.payload },
      };

    case "ADD_INDICATOR":
      return {
        ...state,
        indicators: [...state.indicators, action.payload],
      };

    case "ADD_INDICATOR_WITH_AUTO": {
      const newIndicators = [...state.indicators, action.payload];
      const allAuto = (state.entry.conditions.length === 0 && state.exit.conditions.length === 0) ||
        (state.entry.conditions.every(c => c.id.startsWith("auto_")) &&
         state.exit.conditions.every(c => c.id.startsWith("auto_")));

      if (allAuto) {
        // 전체 auto 상태 → 모든 지표로 재생성
        const auto = generateAutoConditions(newIndicators);
        return {
          ...state,
          indicators: newIndicators,
          entry: { ...state.entry, conditions: auto.entry },
          exit: { ...state.exit, conditions: auto.exit },
        };
      }

      // 수동 조건이 섞여 있음 → 새 지표에 대한 조건만 추가
      const added = generateAutoConditions([action.payload]);
      return {
        ...state,
        indicators: newIndicators,
        entry: { ...state.entry, conditions: [...state.entry.conditions, ...added.entry] },
        exit: { ...state.exit, conditions: [...state.exit.conditions, ...added.exit] },
      };
    }

    case "UPDATE_INDICATOR": {
      // alias는 내부 key — 변경 불가. displayName 등 다른 필드만 업데이트.
      const updates = { ...action.payload.updates };
      delete (updates as Partial<BuilderIndicator>).alias; // alias 변경 무시

      const updatedIndicators = state.indicators.map((ind) =>
        ind.id === action.payload.id ? { ...ind, ...updates } : ind
      );
      return { ...state, indicators: updatedIndicators };
    }

    case "REMOVE_INDICATOR": {
      const removedAlias = state.indicators.find((i) => i.id === action.payload)?.alias;
      const remainingIndicators = state.indicators.filter((ind) => ind.id !== action.payload);

      // 조건이 모두 auto-generated이면 재생성, 아니면 해당 지표 참조 조건만 제거
      const entryAllAutoR = state.entry.conditions.every(c => c.id.startsWith("auto_"));
      const exitAllAutoR = state.exit.conditions.every(c => c.id.startsWith("auto_"));

      if (entryAllAutoR && exitAllAutoR) {
        const auto = generateAutoConditions(remainingIndicators);
        return {
          ...state,
          indicators: remainingIndicators,
          entry: { ...state.entry, conditions: auto.entry },
          exit: { ...state.exit, conditions: auto.exit },
        };
      }

      return {
        ...state,
        indicators: remainingIndicators,
        entry: {
          ...state.entry,
          conditions: state.entry.conditions.filter(
            (c) =>
              c.left.indicatorAlias !== removedAlias &&
              c.right.indicatorAlias !== removedAlias &&
              c.candlestickAlias !== removedAlias
          ),
        },
        exit: {
          ...state.exit,
          conditions: state.exit.conditions.filter(
            (c) =>
              c.left.indicatorAlias !== removedAlias &&
              c.right.indicatorAlias !== removedAlias &&
              c.candlestickAlias !== removedAlias
          ),
        },
      };
    }

    case "ADD_ENTRY_CONDITION":
      return {
        ...state,
        entry: {
          ...state.entry,
          conditions: [...state.entry.conditions, action.payload],
        },
      };

    case "UPDATE_ENTRY_CONDITION":
      return {
        ...state,
        entry: {
          ...state.entry,
          conditions: state.entry.conditions.map((c) =>
            c.id === action.payload.id ? { ...c, ...action.payload.updates } : c
          ),
        },
      };

    case "REMOVE_ENTRY_CONDITION":
      return {
        ...state,
        entry: {
          ...state.entry,
          conditions: state.entry.conditions.filter((c) => c.id !== action.payload),
        },
      };

    case "REORDER_ENTRY_CONDITIONS":
      return {
        ...state,
        entry: {
          ...state.entry,
          conditions: action.payload,
        },
      };

    case "SET_ENTRY_LOGIC":
      return {
        ...state,
        entry: { ...state.entry, logic: action.payload },
      };

    case "ADD_EXIT_CONDITION":
      return {
        ...state,
        exit: {
          ...state.exit,
          conditions: [...state.exit.conditions, action.payload],
        },
      };

    case "UPDATE_EXIT_CONDITION":
      return {
        ...state,
        exit: {
          ...state.exit,
          conditions: state.exit.conditions.map((c) =>
            c.id === action.payload.id ? { ...c, ...action.payload.updates } : c
          ),
        },
      };

    case "REMOVE_EXIT_CONDITION":
      return {
        ...state,
        exit: {
          ...state.exit,
          conditions: state.exit.conditions.filter((c) => c.id !== action.payload),
        },
      };

    case "REORDER_EXIT_CONDITIONS":
      return {
        ...state,
        exit: {
          ...state.exit,
          conditions: action.payload,
        },
      };

    case "SET_EXIT_LOGIC":
      return {
        ...state,
        exit: { ...state.exit, logic: action.payload },
      };

    case "SET_RISK":
      return {
        ...state,
        risk: { ...state.risk, ...action.payload },
      };

    case "AUTO_GENERATE_CONDITIONS":
      return {
        ...state,
        entry: {
          ...state.entry,
          conditions: action.payload.entry,
        },
        exit: {
          ...state.exit,
          conditions: action.payload.exit,
        },
      };

    case "LOAD_STATE": {
      const loaded = action.payload;
      const seenIds = new Set<string>();
      const fixedIndicators = loaded.indicators.map((ind) => {
        if (seenIds.has(ind.id)) {
          return { ...ind, id: `${ind.indicatorId}_${Date.now()}_${randomToken(4)}` };
        }
        seenIds.add(ind.id);
        return ind;
      });

      return { ...loaded, indicators: fixedIndicators };
    }

    case "RESET":
      return INITIAL_STATE;

    default:
      return state;
  }
}

// ============================================================
// Hook
// ============================================================

export function useStrategyBuilder(initialState?: BuilderState) {
  const [state, dispatch] = useReducer(builderReducer, initialState || INITIAL_STATE);

  // ============================================================
  // Metadata Actions
  // ============================================================

  const setMetadata = useCallback((updates: Partial<BuilderMetadata>) => {
    dispatch({ type: "SET_METADATA", payload: updates });
  }, []);

  // ============================================================
  // Indicator Actions
  // ============================================================

  const addIndicator = useCallback((indicator: BuilderIndicator) => {
    dispatch({ type: "ADD_INDICATOR", payload: indicator });
  }, []);

  /** 지표 추가 + 진입/청산 조건 자동 재생성 (조건이 비어있거나 모두 auto인 경우) */
  const addIndicatorWithAutoConditions = useCallback((indicator: BuilderIndicator) => {
    dispatch({ type: "ADD_INDICATOR_WITH_AUTO", payload: indicator });
  }, []);

  const updateIndicator = useCallback(
    (id: string, updates: Partial<BuilderIndicator>) => {
      dispatch({ type: "UPDATE_INDICATOR", payload: { id, updates } });
    },
    []
  );

  const removeIndicator = useCallback((id: string) => {
    dispatch({ type: "REMOVE_INDICATOR", payload: id });
  }, []);

  // ============================================================
  // Entry Condition Actions
  // ============================================================

  const addEntryCondition = useCallback((condition: BuilderCondition) => {
    dispatch({ type: "ADD_ENTRY_CONDITION", payload: condition });
  }, []);

  const updateEntryCondition = useCallback(
    (id: string, updates: Partial<BuilderCondition>) => {
      dispatch({ type: "UPDATE_ENTRY_CONDITION", payload: { id, updates } });
    },
    []
  );

  const removeEntryCondition = useCallback((id: string) => {
    dispatch({ type: "REMOVE_ENTRY_CONDITION", payload: id });
  }, []);

  const setEntryLogic = useCallback((logic: "AND" | "OR") => {
    dispatch({ type: "SET_ENTRY_LOGIC", payload: logic });
  }, []);

  const reorderEntryConditions = useCallback((conditions: BuilderCondition[]) => {
    dispatch({ type: "REORDER_ENTRY_CONDITIONS", payload: conditions });
  }, []);

  // ============================================================
  // Exit Condition Actions
  // ============================================================

  const addExitCondition = useCallback((condition: BuilderCondition) => {
    dispatch({ type: "ADD_EXIT_CONDITION", payload: condition });
  }, []);

  const updateExitCondition = useCallback(
    (id: string, updates: Partial<BuilderCondition>) => {
      dispatch({ type: "UPDATE_EXIT_CONDITION", payload: { id, updates } });
    },
    []
  );

  const removeExitCondition = useCallback((id: string) => {
    dispatch({ type: "REMOVE_EXIT_CONDITION", payload: id });
  }, []);

  const setExitLogic = useCallback((logic: "AND" | "OR") => {
    dispatch({ type: "SET_EXIT_LOGIC", payload: logic });
  }, []);

  const reorderExitConditions = useCallback((conditions: BuilderCondition[]) => {
    dispatch({ type: "REORDER_EXIT_CONDITIONS", payload: conditions });
  }, []);

  // ============================================================
  // Risk Management Actions
  // ============================================================

  const setRisk = useCallback((updates: Partial<RiskManagement>) => {
    dispatch({ type: "SET_RISK", payload: updates });
  }, []);

  // ============================================================
  // State Actions
  // ============================================================

  const loadState = useCallback((newState: BuilderState) => {
    dispatch({ type: "LOAD_STATE", payload: newState });
  }, []);

  const reset = useCallback(() => {
    dispatch({ type: "RESET" });
  }, []);

  // ============================================================
  // YAML Conversion
  // ============================================================

  const toYaml = useMemo((): YamlStrategy => builderStateToYaml(state), [state]);

  const toYamlString = useMemo((): string => builderStateToYamlString(state), [state]);

  // ============================================================
  // Validation
  // ============================================================

  const isValid = useMemo((): boolean => {
    if (!state.metadata.name.trim()) return false;
    if (state.indicators.length === 0) return false;
    if (state.entry.conditions.length === 0) return false;
    if (state.exit.conditions.length === 0) return false;
    return true;
  }, [state]);

  const validationErrors = useMemo((): string[] => {
    const errors: string[] = [];
    if (!state.metadata.name.trim()) {
      errors.push("전략 이름을 입력하세요");
    }
    if (state.indicators.length === 0) {
      errors.push("최소 1개의 지표를 추가하세요");
    }
    if (state.entry.conditions.length === 0) {
      errors.push("진입 조건을 추가하세요");
    }
    if (state.exit.conditions.length === 0) {
      errors.push("청산 조건을 추가하세요");
    }
    return errors;
  }, [state]);

  // ============================================================
  // Helper: Create Indicator with Defaults
  // ============================================================

  const createIndicator = useCallback(
    (indicatorId: string, customAlias?: string): BuilderIndicator | null => {
      const def = getIndicatorById(indicatorId);
      if (!def) return null;

      const defaultParams: Record<string, number | string> = {};
      def.params.forEach((p) => {
        defaultParams[p.name] = p.default;
      });

      const existingCount = state.indicators.filter((i) => i.indicatorId === indicatorId).length;
      const existingAliases = new Set(state.indicators.map((i) => i.alias));

      // alias는 항상 깔끔한 Python 식별자로 자동 생성 (사용자 입력 무관)
      const baseAlias = `${indicatorId}_${existingCount + 1}`;
      const resolveAlias = (base: string): string => {
        if (!existingAliases.has(base)) return base;
        let n = 2;
        while (existingAliases.has(`${base}_${n}`)) n++;
        return `${base}_${n}`;
      };
      const alias = resolveAlias(baseAlias);
      // customAlias는 displayName으로 사용 (표시용)
      const displayName = customAlias !== alias ? customAlias : undefined;

      // 고유 ID 생성: timestamp + random string (중복 방지)
      const uniqueId = `${indicatorId}_${Date.now()}_${randomToken(4)}`;

      return {
        id: uniqueId,
        indicatorId,
        alias,
        ...(displayName ? { displayName } : {}),
        params: defaultParams,
        output: def.defaultOutput,
      };
    },
    [state.indicators]
  );

  // ============================================================
  // Return
  // ============================================================

  return {
    state,
    // Metadata
    setMetadata,
    // Indicators
    addIndicator,
    addIndicatorWithAutoConditions,
    updateIndicator,
    removeIndicator,
    createIndicator,
    // Entry Conditions
    addEntryCondition,
    updateEntryCondition,
    removeEntryCondition,
    reorderEntryConditions,
    setEntryLogic,
    // Exit Conditions
    addExitCondition,
    updateExitCondition,
    removeExitCondition,
    reorderExitConditions,
    setExitLogic,
    // Risk
    setRisk,
    // State
    loadState,
    reset,
    // YAML
    toYaml,
    toYamlString,
    // Validation
    isValid,
    validationErrors,
  };
}

export type UseStrategyBuilderReturn = ReturnType<typeof useStrategyBuilder>;
