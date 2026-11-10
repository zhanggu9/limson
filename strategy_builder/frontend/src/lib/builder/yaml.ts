/**
 * 빌더 상태(BuilderState) → .kis.yaml 변환
 *
 * 빌더 화면(useStrategyBuilder)과 저장소(storage)가 같이 쓴다.
 * 저장할 때 YAML도 함께 넣어 두면, 같은 브라우저의 백테스터가 "내 전략"으로 읽어 간다.
 */

import type {
  BuilderState,
  BuilderCondition,
  YamlStrategy,
  YamlIndicator,
  YamlCandlestick,
  YamlCondition,
} from "@/types/builder";
import { CANDLESTICK_PATTERNS } from "@/lib/builder/constants";

// 캔들스틱 패턴 ID Set (빠른 lookup)
const CANDLESTICK_IDS = new Set(CANDLESTICK_PATTERNS.map(p => p.id));

/** YAML 큰따옴표 문자열 (JSON 문자열은 그대로 YAML 큰따옴표 문자열이 된다) */
function quote(value: string | undefined): string {
  return JSON.stringify(value ?? "");
}

// 백테스터 스키마와 같은 규칙: 영문·숫자로 시작, 영문·숫자·_·- 만, 64자 이하
const STRATEGY_ID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** 이름이 한글이어도 백테스터가 받는 영문 ID를 만든다 (같은 이름이면 같은 ID) */
export function strategyId(state: BuilderState): string {
  if (STRATEGY_ID_RE.test(state.metadata.id)) return state.metadata.id;
  const name = state.metadata.name.trim();
  const ascii = name
    .toLowerCase()
    .replace(/\s+/g, "_")
    .replace(/[^a-z0-9_-]/g, "")
    .replace(/^[_-]+|[_-]+$/g, "")
    .slice(0, 64);
  if (ascii && ascii === name.toLowerCase().replace(/\s+/g, "_")) return ascii;
  // 한글·특수문자가 섞인 이름: 이름의 짧은 해시를 붙여 서로 다른 이름이 같은 ID가 되지 않게 한다
  let hash = 2166136261;
  for (const ch of name) {
    hash ^= ch.codePointAt(0) ?? 0;
    hash = Math.imul(hash, 16777619) >>> 0;
  }
  const suffix = hash.toString(36);
  return `${(ascii || "custom").slice(0, 50)}_${suffix}`;
}

export function builderStateToYaml(state: BuilderState): YamlStrategy {
  // 캔들스틱과 일반 지표 분리
  const indicatorItems = state.indicators.filter(ind => !CANDLESTICK_IDS.has(ind.indicatorId));
  const candlestickItems = state.indicators.filter(ind => CANDLESTICK_IDS.has(ind.indicatorId));

  // 일반 지표 YAML
  const yamlIndicators: YamlIndicator[] = indicatorItems.map((ind) => ({
    id: ind.indicatorId,
    alias: ind.alias,
    ...(ind.displayName ? { name: ind.displayName } : {}),
    params: Object.fromEntries(
      Object.entries(ind.params).map(([k, v]) => {
        if (typeof v === "number") return [k, v];
        if (typeof v === "string") {
          const num = parseFloat(v);
          return [k, isNaN(num) ? v : num];
        }
        return [k, v];
      })
    ),
    output: ind.output !== "value" ? ind.output : undefined,
  }));

  // 캔들스틱 YAML
  const yamlCandlesticks: YamlCandlestick[] = candlestickItems.map((ind) => ({
    id: ind.indicatorId,
    alias: ind.alias,
  }));

  // 캔들스틱 alias Set (조건 변환에서 사용)
  const candlestickAliases = new Set(candlestickItems.map(c => c.alias));

  const convertCondition = (c: BuilderCondition): YamlCondition => {
    const left = c.left;
    const right = c.right;

    // 새로운 캔들스틱 조건 형식 (isCandlestick 플래그 사용)
    if (c.isCandlestick && c.candlestickAlias) {
      return {
        candlestick: c.candlestickAlias,
        signal: c.candlestickSignal || "detected",
      };
    }

    // 레거시: 왼쪽이 캔들스틱인 경우 (이전 버전 호환)
    if (left.type === "indicator" && left.indicatorAlias && candlestickAliases.has(left.indicatorAlias)) {
      // 캔들스틱 조건: 비교 대상에 따라 signal 결정
      // 기본적으로 > 0 비교면 bullish, < 0 비교면 bearish
      let signal: "bullish" | "bearish" | "detected" = "detected";
      if (right.type === "value" && right.value !== undefined) {
        if (c.operator === "greater_than" && right.value === 0) {
          signal = "bullish";
        } else if (c.operator === "less_than" && right.value === 0) {
          signal = "bearish";
        }
      }
      return {
        candlestick: left.indicatorAlias,
        signal,
      };
    }

    let indicatorName = "";
    let compareTo: string | number = 0;
    let output: string | undefined;
    let compareOutput: string | undefined;

    // Left operand
    if (left.type === "indicator") {
      indicatorName = left.indicatorAlias || "";
      if (left.indicatorOutput && left.indicatorOutput !== "value") {
        output = left.indicatorOutput;
      }
    } else if (left.type === "price") {
      indicatorName = left.priceField || "close";
    }

    // Right operand
    if (right.type === "indicator") {
      compareTo = right.indicatorAlias || "";
      // 오른쪽 지표의 output도 캡처 (예: macd의 signal)
      if (right.indicatorOutput && right.indicatorOutput !== "value") {
        compareOutput = right.indicatorOutput;
      }
    } else if (right.type === "value") {
      compareTo = right.value ?? 0;
    } else if (right.type === "price") {
      compareTo = right.priceField || "close";
    }

    // Map operator to canonical YAML form
    const operatorMap: Record<string, string> = {
      greater_than: "greater_than",
      less_than: "less_than",
      greater_equal: "greater_equal",
      less_equal: "less_equal",
      cross_above: "cross_above",
      cross_below: "cross_below",
      equals: "equals",
    };

    return {
      indicator: indicatorName,
      operator: operatorMap[c.operator] || c.operator,
      compare_to: compareTo,
      output,
      compare_output: compareOutput,
    };
  };

  return {
    version: "1.0",
    metadata: {
      name: state.metadata.name,
      description: state.metadata.description,
      author: state.metadata.author,
      tags: state.metadata.tags,
    },
    strategy: {
      id: strategyId(state),
      category: state.metadata.category,
      indicators: yamlIndicators,
      candlesticks: yamlCandlesticks.length > 0 ? yamlCandlesticks : undefined,
      entry: {
        logic: state.entry.logic,
        conditions: state.entry.conditions.map(convertCondition),
      },
      exit: {
        logic: state.exit.logic,
        conditions: state.exit.conditions.map(convertCondition),
      },
    },
    risk: {
      stop_loss: state.risk.stopLoss.enabled
        ? { enabled: true, percent: state.risk.stopLoss.percent }
        : undefined,
      take_profit: state.risk.takeProfit.enabled
        ? { enabled: true, percent: state.risk.takeProfit.percent }
        : undefined,
      trailing_stop: state.risk.trailingStop.enabled
        ? { enabled: true, percent: state.risk.trailingStop.percent }
        : undefined,
    },
  };
}

export function builderStateToYamlString(state: BuilderState): string {
  const yaml = builderStateToYaml(state);

  // Simple YAML serializer
  const lines: string[] = [];
  lines.push(`version: "${yaml.version}"`);
  lines.push("");
  lines.push("metadata:");
  lines.push(`  name: ${quote(yaml.metadata.name)}`);
  lines.push(`  description: ${quote(yaml.metadata.description)}`);
  if (yaml.metadata.author) {
    lines.push(`  author: ${quote(yaml.metadata.author)}`);
  }
  if (yaml.metadata.tags.length > 0) {
    lines.push("  tags:");
    yaml.metadata.tags.forEach((tag) => lines.push(`    - ${quote(tag)}`));
  } else {
    lines.push("  tags: []");
  }
  lines.push("");
  lines.push("strategy:");
  lines.push(`  id: ${yaml.strategy.id}`);
  lines.push(`  category: ${yaml.strategy.category}`);
  lines.push("");
  lines.push("  indicators:");
  if (yaml.strategy.indicators.length > 0) {
    yaml.strategy.indicators.forEach((ind) => {
      lines.push(`    - id: ${ind.id}`);
      lines.push(`      alias: ${ind.alias}`);
      if (Object.keys(ind.params).length > 0) {
        lines.push("      params:");
        Object.entries(ind.params).forEach(([k, v]) => {
          lines.push(`        ${k}: ${v}`);
        });
      }
      if (ind.output) {
        lines.push(`      output: ${ind.output}`);
      }
    });
  } else {
    lines.push("    []");
  }

  // 캔들스틱 패턴 출력
  if (yaml.strategy.candlesticks && yaml.strategy.candlesticks.length > 0) {
    lines.push("");
    lines.push("  candlesticks:");
    yaml.strategy.candlesticks.forEach((c) => {
      lines.push(`    - id: ${c.id}`);
      lines.push(`      alias: ${c.alias}`);
    });
  }
  lines.push("");
  lines.push("  entry:");
  lines.push(`    logic: ${yaml.strategy.entry.logic}`);
  lines.push("    conditions:");
  yaml.strategy.entry.conditions.forEach((c) => {
    // 캔들스틱 조건
    if (c.candlestick) {
      lines.push(`      - candlestick: ${c.candlestick}`);
      lines.push(`        signal: ${c.signal || "detected"}`);
    } else {
      // 일반 지표 조건
      lines.push(`      - indicator: ${c.indicator || ""}`);
      lines.push(`        operator: ${c.operator || "gt"}`);
      lines.push(`        compare_to: ${c.compare_to ?? 0}`);
      if (c.output) {
        lines.push(`        output: ${c.output}`);
      }
      if (c.compare_output) {
        lines.push(`        compare_output: ${c.compare_output}`);
      }
    }
  });
  lines.push("");
  lines.push("  exit:");
  lines.push(`    logic: ${yaml.strategy.exit.logic}`);
  lines.push("    conditions:");
  yaml.strategy.exit.conditions.forEach((c) => {
    // 캔들스틱 조건
    if (c.candlestick) {
      lines.push(`      - candlestick: ${c.candlestick}`);
      lines.push(`        signal: ${c.signal || "detected"}`);
    } else {
      // 일반 지표 조건
      lines.push(`      - indicator: ${c.indicator || ""}`);
      lines.push(`        operator: ${c.operator || "gt"}`);
      lines.push(`        compare_to: ${c.compare_to ?? 0}`);
      if (c.output) {
        lines.push(`        output: ${c.output}`);
      }
      if (c.compare_output) {
        lines.push(`        compare_output: ${c.compare_output}`);
      }
    }
  });
  lines.push("");
  // risk 섹션: 활성화된 설정이 하나라도 있으면 출력, 없으면 빈 객체
  const hasRisk = yaml.risk.stop_loss || yaml.risk.take_profit || yaml.risk.trailing_stop;
  if (hasRisk) {
    lines.push("risk:");
    if (yaml.risk.stop_loss) {
      lines.push("  stop_loss:");
      lines.push(`    enabled: ${yaml.risk.stop_loss.enabled}`);
      lines.push(`    percent: ${yaml.risk.stop_loss.percent}`);
    }
    if (yaml.risk.take_profit) {
      lines.push("  take_profit:");
      lines.push(`    enabled: ${yaml.risk.take_profit.enabled}`);
      lines.push(`    percent: ${yaml.risk.take_profit.percent}`);
    }
    if (yaml.risk.trailing_stop) {
      lines.push("  trailing_stop:");
      lines.push(`    enabled: ${yaml.risk.trailing_stop.enabled}`);
      lines.push(`    percent: ${yaml.risk.trailing_stop.percent}`);
    }
  } else {
    // risk 설정이 없으면 빈 객체로 출력 (YAML에서 null 방지)
    lines.push("risk: {}");
  }

  return lines.join("\n");
}
