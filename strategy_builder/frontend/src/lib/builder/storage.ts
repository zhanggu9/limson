/**
 * localStorage Helper for Custom Strategies
 */

import type { StoredStrategy, BuilderState } from "@/types/builder";
import { randomToken } from "@/lib/utils";
import { builderStateToYamlString } from "@/lib/builder/yaml";

const STORAGE_KEY = "kis2_custom_strategies";
const MAX_STRATEGIES = 50;

/**
 * Generate unique ID for new strategy
 */
function generateId(): string {
  return `custom_${Date.now()}_${randomToken(4)}`;
}

/**
 * Load all custom strategies from localStorage
 */
export function loadAllStrategies(): StoredStrategy[] {
  if (typeof window === "undefined") {
    return [];
  }

  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) {
      return [];
    }
    const strategies = syncYaml(JSON.parse(stored) as StoredStrategy[]);
    return strategies.sort(
      (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    );
  } catch {
    return [];
  }
}

function toYamlSafe(state: BuilderState): string | undefined {
  try {
    return builderStateToYamlString(state);
  } catch {
    return undefined;
  }
}

/**
 * 저장된 전략마다 YAML을 지금 변환 규칙으로 다시 만들고, 달라진 게 있으면 저장한다.
 * (YAML 필드가 생기기 전에 저장한 전략도 빌더를 한 번 열면 백테스터에서 보이게 된다)
 */
function syncYaml(strategies: StoredStrategy[]): StoredStrategy[] {
  let changed = false;
  const synced = strategies.map((s) => {
    const yaml = toYamlSafe(s.state);
    if (yaml === s.yaml) return s;
    changed = true;
    return { ...s, yaml };
  });
  if (changed) persistStrategies(synced);
  return synced;
}

/**
 * Get a single strategy by ID
 */
export function getStrategy(id: string): StoredStrategy | null {
  const strategies = loadAllStrategies();
  return strategies.find((s) => s.id === id) || null;
}

/**
 * Save a new strategy or update existing one
 */
export function saveStrategy(state: BuilderState, existingId?: string): StoredStrategy {
  const strategies = loadAllStrategies();
  const now = new Date().toISOString();

  if (existingId) {
    // Update existing strategy
    const index = strategies.findIndex((s) => s.id === existingId);
    if (index !== -1) {
      const updated: StoredStrategy = {
        ...strategies[index],
        name: state.metadata.name,
        updatedAt: now,
        state,
        yaml: toYamlSafe(state),
      };
      const newStrategies = [...strategies];
      newStrategies[index] = updated;
      persistStrategies(newStrategies);
      return updated;
    }
  }

  // Create new strategy
  const newStrategy: StoredStrategy = {
    id: generateId(),
    name: state.metadata.name,
    createdAt: now,
    updatedAt: now,
    state,
    yaml: toYamlSafe(state),
  };

  // Enforce max limit
  const newStrategies = [newStrategy, ...strategies].slice(0, MAX_STRATEGIES);
  persistStrategies(newStrategies);
  return newStrategy;
}

/**
 * Delete a strategy by ID
 */
export function deleteStrategy(id: string): boolean {
  const strategies = loadAllStrategies();
  const filtered = strategies.filter((s) => s.id !== id);

  if (filtered.length === strategies.length) {
    return false; // Not found
  }

  persistStrategies(filtered);
  return true;
}

/**
 * Duplicate a strategy
 */
export function duplicateStrategy(id: string): StoredStrategy | null {
  const original = getStrategy(id);
  if (!original) {
    return null;
  }

  const newState: BuilderState = {
    ...original.state,
    metadata: {
      ...original.state.metadata,
      id: generateId(),
      name: `${original.name} (복사본)`,
    },
  };

  return saveStrategy(newState);
}

/**
 * Export strategy as JSON string
 */
export function exportAsJson(id: string): string | null {
  const strategy = getStrategy(id);
  if (!strategy) {
    return null;
  }
  return JSON.stringify(strategy.state, null, 2);
}

/**
 * Import strategy from JSON string
 */
export function importFromJson(json: string): StoredStrategy | null {
  try {
    const state = JSON.parse(json) as BuilderState;
    // Validate required fields
    if (!state.metadata?.name || !state.indicators || !state.entry || !state.exit) {
      return null;
    }
    return saveStrategy(state);
  } catch {
    return null;
  }
}

/**
 * Clear all custom strategies
 */
export function clearAllStrategies(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(STORAGE_KEY);
  }
}

/**
 * Internal: Persist strategies to localStorage
 */
function persistStrategies(strategies: StoredStrategy[]): void {
  if (typeof window === "undefined") {
    return;
  }

  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(strategies));
  } catch (e) {
    // localStorage is full or unavailable
    if (e instanceof Error && e.name === "QuotaExceededError") {
      // Remove oldest strategies to make room
      const reduced = strategies.slice(0, Math.floor(strategies.length / 2));
      localStorage.setItem(STORAGE_KEY, JSON.stringify(reduced));
    }
  }
}

/**
 * Check if localStorage is available
 */
export function isStorageAvailable(): boolean {
  if (typeof window === "undefined") {
    return false;
  }

  try {
    const test = "__storage_test__";
    localStorage.setItem(test, test);
    localStorage.removeItem(test);
    return true;
  } catch {
    return false;
  }
}

/**
 * Get storage usage info
 */
export function getStorageInfo(): { used: number; count: number } {
  if (typeof window === "undefined") {
    return { used: 0, count: 0 };
  }

  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    const strategies = stored ? (JSON.parse(stored) as StoredStrategy[]) : [];
    return {
      used: stored ? new Blob([stored]).size : 0,
      count: strategies.length,
    };
  } catch {
    return { used: 0, count: 0 };
  }
}
