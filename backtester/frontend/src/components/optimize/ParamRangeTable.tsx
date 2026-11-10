"use client";

import { cn } from "@/lib/utils";
import { PARAM_GROUP_LABELS, rowError, rowGridSize, type ParamRow } from "@/lib/hyperopt";
import type { HyperoptSpace } from "@/types";

interface ParamRangeTableProps {
  space: HyperoptSpace;
  rows: Record<string, ParamRow>;
  onChange: (name: string, patch: Partial<ParamRow>) => void;
  disabled?: boolean;
}

const inputClass =
  "w-full min-w-0 px-2 py-1 border border-slate-200 dark:border-slate-700 rounded-md text-sm font-mono tabular-nums bg-white dark:bg-slate-900 disabled:opacity-50";

/** 파라미터마다 "찾을지 / 범위 / 간격" 또는 "고정값"을 정하는 표 */
export function ParamRangeTable({ space, rows, onChange, disabled }: ParamRangeTableProps) {
  if (space.params.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        이 전략에는 바꿔 볼 숫자가 없습니다. 전략 빌더에서 지표 기간이나 조건 값을 숫자로 넣은 전략을 고르세요.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-slate-500 border-b border-slate-200 dark:border-slate-700">
            <th className="pb-2 pr-2 font-medium w-14">찾기</th>
            <th className="pb-2 pr-3 font-medium">파라미터</th>
            <th className="pb-2 pr-3 font-medium text-right w-20">기본값</th>
            <th className="pb-2 pr-2 font-medium w-24">최소</th>
            <th className="pb-2 pr-2 font-medium w-24">최대</th>
            <th className="pb-2 pr-2 font-medium w-20">간격</th>
            <th className="pb-2 font-medium text-right w-16">값 수</th>
          </tr>
        </thead>
        <tbody>
          {space.params.map((spec) => {
            const row = rows[spec.name];
            if (!row) return null;
            const error = rowError(spec, row);
            return (
              <tr key={spec.name} className="border-b border-slate-100 dark:border-slate-800 last:border-0 align-top">
                <td className="py-2 pr-2">
                  <input
                    type="checkbox"
                    checked={row.optimize}
                    disabled={disabled}
                    onChange={(e) => onChange(spec.name, { optimize: e.target.checked })}
                    aria-label={`${spec.label} 찾기`}
                    className="w-4 h-4 accent-[#245bee]"
                  />
                </td>
                <td className="py-2 pr-3">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-slate-800 dark:text-slate-200">{spec.label}</span>
                    <span className="text-[10px] text-slate-500 bg-slate-100 dark:bg-slate-800 rounded px-1 py-0.5">
                      {PARAM_GROUP_LABELS[spec.group]}
                    </span>
                  </div>
                  <div className="text-[11px] text-slate-400 font-mono">{spec.name}</div>
                  {error && <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">{error}</p>}
                </td>
                <td className="py-2 pr-3 text-right font-mono tabular-nums text-slate-600 dark:text-slate-400">
                  {spec.default}
                </td>
                {row.optimize ? (
                  <>
                    <td className="py-2 pr-2">
                      <input
                        type="number"
                        value={row.low}
                        step={spec.type === "int" ? 1 : "any"}
                        disabled={disabled}
                        onChange={(e) => onChange(spec.name, { low: e.target.value })}
                        aria-label={`${spec.label} 최소`}
                        className={inputClass}
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="number"
                        value={row.high}
                        step={spec.type === "int" ? 1 : "any"}
                        disabled={disabled}
                        onChange={(e) => onChange(spec.name, { high: e.target.value })}
                        aria-label={`${spec.label} 최대`}
                        className={inputClass}
                      />
                    </td>
                    <td className="py-2 pr-2">
                      <input
                        type="number"
                        value={row.step}
                        min={0}
                        step={spec.type === "int" ? 1 : "any"}
                        disabled={disabled}
                        onChange={(e) => onChange(spec.name, { step: e.target.value })}
                        aria-label={`${spec.label} 간격`}
                        className={inputClass}
                      />
                    </td>
                    <td className="py-2 text-right font-mono tabular-nums text-xs text-slate-500">
                      {error ? "-" : rowGridSize(row).toLocaleString()}
                    </td>
                  </>
                ) : (
                  <>
                    <td className="py-2 pr-2" colSpan={3}>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-slate-500 shrink-0">고정</span>
                        <input
                          type="number"
                          value={row.value}
                          step={spec.type === "int" ? 1 : "any"}
                          disabled={disabled}
                          onChange={(e) => onChange(spec.name, { value: e.target.value })}
                          aria-label={`${spec.label} 고정값`}
                          className={cn(inputClass, "max-w-[8rem]")}
                        />
                      </div>
                    </td>
                    <td className="py-2 text-right text-xs text-slate-400">1</td>
                  </>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
