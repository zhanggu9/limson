/**
 * 전략 빌더에서 저장한 "내 전략" 읽기
 *
 * 전략 빌더는 전략을 브라우저 localStorage(kis2_custom_strategies)에 저장하고, 저장할 때 .kis.yaml도 함께 넣는다.
 * 배포 환경에서는 빌더(/builder)와 백테스터(/backtest)가 같은 주소(origin)라 같은 저장소를 읽을 수 있다.
 * 로컬 개발처럼 포트가 다르면 저장소가 달라서 목록이 비어 보인다.
 */

import { useMemo, useSyncExternalStore } from "react";

export const BUILDER_STRATEGIES_KEY = "kis2_custom_strategies";

export interface MyStrategy {
  id: string;
  name: string;
  updatedAt: string;
  /** 빌더가 넣어 둔 YAML. 예전에 저장해서 아직 없으면 null (빌더를 한 번 열면 채워진다) */
  yaml: string | null;
}

interface StoredStrategyLike {
  id?: unknown;
  name?: unknown;
  updatedAt?: unknown;
  yaml?: unknown;
}

function readRaw(): string | null {
  try {
    return window.localStorage.getItem(BUILDER_STRATEGIES_KEY);
  } catch {
    return null;
  }
}

export function parseMyStrategies(raw: string | null | undefined): MyStrategy[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item): item is StoredStrategyLike => typeof item === "object" && item !== null)
      .filter((item) => typeof item.id === "string")
      .map((item) => ({
        id: item.id as string,
        name: typeof item.name === "string" && item.name.trim() ? item.name : "이름 없는 전략",
        updatedAt: typeof item.updatedAt === "string" ? item.updatedAt : "",
        yaml: typeof item.yaml === "string" && item.yaml.trim() ? item.yaml : null,
      }))
      .sort((a, b) => (Date.parse(b.updatedAt) || 0) - (Date.parse(a.updatedAt) || 0));
  } catch {
    return [];
  }
}

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === null || event.key === BUILDER_STRATEGIES_KEY) onChange();
  };
  window.addEventListener("storage", onStorage);
  document.addEventListener("visibilitychange", onChange);
  return () => {
    window.removeEventListener("storage", onStorage);
    document.removeEventListener("visibilitychange", onChange);
  };
}

/** 내 전략 목록. 다른 탭의 빌더에서 저장하거나 이 탭으로 돌아오면 다시 읽는다. */
export function useMyStrategies(): { strategies: MyStrategy[]; loaded: boolean } {
  // 서버 렌더링에서는 undefined (아직 못 읽음), 브라우저에서는 저장된 문자열 또는 null
  const raw = useSyncExternalStore<string | null | undefined>(subscribe, readRaw, () => undefined);
  const strategies = useMemo(() => parseMyStrategies(raw), [raw]);
  return { strategies, loaded: raw !== undefined };
}

export const STRATEGY_BUILDER_URL = process.env.NEXT_PUBLIC_STRATEGY_BUILDER_URL || "http://localhost:3000/builder";
