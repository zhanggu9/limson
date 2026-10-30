/**
 * 팩터 (벡터화 백테스트) API
 */

import { apiPost } from "./client";
import type { MomentumFactorRequest, MomentumFactorResponse } from "@/types";

export async function runMomentumFactor(request: MomentumFactorRequest): Promise<MomentumFactorResponse> {
  return apiPost("/api/factor/momentum", request);
}
