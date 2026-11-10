/**
 * 하이퍼옵트 API
 */

import { API_BASE, apiGet, apiPost } from "./client";
import type { HyperoptEvent, HyperoptRequest, HyperoptSpace } from "@/types";

/** 전략에서 바꿔 볼 수 있는 파라미터와 기본 범위 */
export async function getHyperoptSpace(body: { strategy_id?: string; yaml_content?: string }): Promise<HyperoptSpace> {
  const res = await apiPost<{ success: boolean; data: HyperoptSpace }>("/api/optimize/space", body);
  return res.data;
}

/** 지금 하이퍼옵트가 돌고 있는지 */
export async function getHyperoptStatus(): Promise<{ running: boolean; max_concurrency: number }> {
  return apiGet("/api/optimize/status");
}

/** FastAPI 오류 응답(detail: 문자열 또는 검증 오류 목록)을 한 줄로 */
function errorMessage(body: unknown, status: number): string {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String((item as { msg: unknown }).msg) : String(item)))
      .join(" / ");
  }
  return `API Error: ${status}`;
}

/**
 * 하이퍼옵트 실행. 서버가 한 줄씩 보내는 진행 이벤트(NDJSON)를 onEvent로 넘긴다.
 * signal로 중단하면 연결이 끊기고 서버도 돌던 백테스트를 멈춘다.
 */
export async function runHyperopt(
  request: HyperoptRequest,
  onEvent: (event: HyperoptEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const res = await fetch(`${API_BASE}/api/optimize/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
    signal,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(errorMessage(body, res.status));
  }
  if (!res.body) throw new Error("서버 응답을 읽을 수 없습니다.");

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline >= 0) {
      const line = buffer.slice(0, newline).trim();
      buffer = buffer.slice(newline + 1);
      if (line) onEvent(JSON.parse(line) as HyperoptEvent);
      newline = buffer.indexOf("\n");
    }
  }
  const rest = (buffer + decoder.decode()).trim();
  if (rest) onEvent(JSON.parse(rest) as HyperoptEvent);
}
