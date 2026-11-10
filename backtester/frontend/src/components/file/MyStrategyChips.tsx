"use client";

import { ExternalLink } from "lucide-react";
import { cn } from "@/lib/utils";
import { STRATEGY_BUILDER_URL, type MyStrategy } from "@/lib/myStrategies";

interface MyStrategyChipsProps {
  strategies: MyStrategy[];
  loaded: boolean;
  /** 지금 쓰는 YAML (같은 YAML인 전략을 선택된 것으로 표시) */
  selectedYaml: string | null;
  onSelect: (strategy: MyStrategy | null) => void;
  disabled?: boolean;
}

/** 전략 선택 카드 안의 "내 전략 (전략 빌더)" 묶음 */
export function MyStrategyChips({ strategies, loaded, selectedYaml, onSelect, disabled }: MyStrategyChipsProps) {
  if (!loaded) return null;
  return (
    <div>
      <p className="text-[11px] font-medium text-slate-400 mb-1">내 전략 (전략 빌더)</p>
      {strategies.length === 0 ? (
        <p className="text-xs text-slate-500">
          전략 빌더에서 저장한 전략이 여기에 보입니다.{" "}
          <a href={STRATEGY_BUILDER_URL} className="inline-flex items-center gap-0.5 text-kis-blue hover:underline">
            전략 빌더 열기
            <ExternalLink className="w-3 h-3" aria-hidden="true" />
          </a>
        </p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {strategies.map((s) => {
            const selected = s.yaml !== null && s.yaml === selectedYaml;
            const unavailable = s.yaml === null;
            return (
              <button
                key={s.id}
                type="button"
                disabled={disabled || unavailable}
                onClick={() => onSelect(selected ? null : s)}
                title={unavailable ? "전략 빌더를 한 번 열면 쓸 수 있습니다." : `전략 빌더에서 저장한 전략${s.updatedAt ? ` · ${s.updatedAt.slice(0, 10)} 수정` : ""}`}
                aria-pressed={selected}
                className={cn(
                  "px-2.5 py-1 rounded-full border text-xs transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
                  selected
                    ? "bg-emerald-600 border-emerald-600 text-white"
                    : "border-emerald-200 dark:border-emerald-900 text-emerald-800 dark:text-emerald-300 hover:border-emerald-500"
                )}
              >
                {s.name}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
