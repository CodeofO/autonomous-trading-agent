import { ApiError, get } from "../api/client";
import type { LlmProfiles, LlmTestEndpoint, MarketCode, PushStatus, ScheduleInfo } from "../api/types";
import { SCHEDULE_API } from "../utils/schedule";

/*
 * 설정 화면 위쪽 카드들의 쿼리 (카드와 설정 화면의 '다 받았는지' 확인이 같은 정의를 쓴다 — 같은 캐시, 요청은 한 번)
 */
const is404 = (e: unknown) => e instanceof ApiError && e.status === 404;

export const TEST_URL = "/api/llm/test-endpoint";
export const US_PROFILE_URL = "/api/us/llm/profile";
export const US_PROFILE_KEY = ["US", "llm-profile"];

export const llmProfileQuery = {
  queryKey: ["llm-profile"],
  queryFn: () => get<LlmProfiles>("/api/llm/profile"),
};

export const usLlmProfileQuery = {
  queryKey: US_PROFILE_KEY,
  queryFn: () => get<LlmProfiles>(US_PROFILE_URL),
};

export const testEndpointQuery = {
  queryKey: ["llm-test-endpoint"],
  queryFn: () => get<LlmTestEndpoint>(TEST_URL),
};

export const pushQuery = {
  queryKey: ["push"],
  queryFn: () => get<PushStatus>("/api/push"),
};

export const scheduleKey = (m: MarketCode) => ["schedule", m];

/** 판단 주기 — 국장은 한 번 더 시도(예전 서버 404 는 빼고), 미장은 꺼져 있으면 404 라 다시 시도하지 않는다 */
export const scheduleQuery = (m: MarketCode) => ({
  queryKey: scheduleKey(m),
  queryFn: () => get<ScheduleInfo>(SCHEDULE_API[m]),
  retry: m === "KR" ? (n: number, e: unknown) => !is404(e) && n < 1 : false,
});
