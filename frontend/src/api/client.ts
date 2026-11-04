import type { MarketCode } from "./types";

const TOKEN_KEY = "paper-trader-token";

// 브라우저 저장소는 막혀 있을 수 있으므로 항상 try/catch, 실패 시 메모리 보관
let memoryToken: string | null = null;

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

export function setToken(token: string | null): void {
  memoryToken = token;
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 저장소 사용 불가 — 메모리 토큰만 유지 */
  }
}

export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" ? detail : `요청 실패 (${status})`);
    this.status = status;
    this.detail = detail;
  }
}

let onUnauthorized: () => void = () => {};
export function setUnauthorizedHandler(fn: () => void): void {
  onUnauthorized = fn;
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(init.json);
  }
  const res = await fetch(path, { ...init, headers, body });
  if (res.status === 401 && !path.startsWith("/api/auth/")) {
    setToken(null);
    onUnauthorized();
  }
  const text = await res.text();
  const data = text ? JSON.parse(text) : null;
  if (!res.ok) throw new ApiError(res.status, data?.detail ?? data);
  return data as T;
}

export const get = <T>(path: string) => api<T>(path);
export const post = <T>(path: string, json?: unknown) => api<T>(path, { method: "POST", json: json ?? {} });
export const put = <T>(path: string, json: unknown) => api<T>(path, { method: "PUT", json });
export const del = <T>(path: string) => api<T>(path, { method: "DELETE" });

export function errorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (Array.isArray(e.detail)) {
      return e.detail
        .map((d: { loc?: string[]; msg?: string }) => `${(d.loc ?? []).slice(-1)[0] ?? ""} ${d.msg ?? ""}`)
        .join(", ");
    }
    return e.message;
  }
  return e instanceof Error ? e.message : String(e);
}

// ---------- 시장별 주소 (국장 /api/… · 미장 /api/us/…) ----------
// 계좌·주문·에이전트처럼 시장마다 따로 있는 API 는 미장일 때 /api/us/… 로 바꿔 부른다.
// 아래 공통 API 는 시장과 관계없이 /api/… 그대로 (미장 매니저 LLM 은 /api/us/llm/profile 을 직접 부른다)
const GLOBAL_PATHS = [
  "/api/health",
  "/api/auth",
  "/api/push",
  "/api/llm",
  "/api/market/indicators",
  "/api/markets",
  "/api/overview",
  "/api/us",
];

/** 경로가 prefix 와 같거나 prefix 뒤에 / ? 가 이어지는지 ("/api/market" 은 "/api/markets" 와 다르다) */
const under = (path: string, prefix: string) =>
  path === prefix || path.startsWith(`${prefix}/`) || path.startsWith(`${prefix}?`);

/** 시장과 관계없는 공통 API 인지 (이미 /api/us/… 인 주소도 그대로 둔다) */
export const isGlobalPath = (path: string) => GLOBAL_PATHS.some((p) => under(path, p));

/** 국장 주소(/api/…)를 그 시장의 주소로: 미장이면 /api/us/… (공통 API·이미 바뀐 주소·/api 밖은 그대로) */
export function marketPath(path: string, market: MarketCode): string {
  if (market !== "US" || !under(path, "/api") || path === "/api" || isGlobalPath(path)) return path;
  return `/api/us${path.slice(4)}`;
}

export interface MarketApi {
  market: MarketCode;
  /** 그 시장의 주소 */
  path: (path: string) => string;
  /** 그 시장 쿼리 키: ["US", "portfolio"] — 시장이 다르면 캐시도 따로 */
  key: (...parts: unknown[]) => unknown[];
  get: <T>(path: string) => Promise<T>;
  post: <T>(path: string, json?: unknown) => Promise<T>;
  put: <T>(path: string, json: unknown) => Promise<T>;
  del: <T>(path: string) => Promise<T>;
  request: <T>(path: string, init?: RequestInit & { json?: unknown }) => Promise<T>;
}

const apis = new Map<MarketCode, MarketApi>();

/** 시장 하나에 묶인 API 묶음 (시장마다 한 번만 만든다 — 훅 의존성으로 써도 바뀌지 않는다) */
export function marketApi(market: MarketCode): MarketApi {
  const hit = apis.get(market);
  if (hit) return hit;
  const path = (p: string) => marketPath(p, market);
  const made: MarketApi = {
    market,
    path,
    key: (...parts) => [market, ...parts],
    get: (p) => get(path(p)),
    post: (p, json) => post(path(p), json),
    put: (p, json) => put(path(p), json),
    del: (p) => del(path(p)),
    request: (p, init) => api(path(p), init),
  };
  apis.set(market, made);
  return made;
}

/** 실시간 이벤트 주소: 국장 /ws/events · 미장 /ws/events?market=US (로그인 중이면 token 도) */
export function eventsUrl(
  market: MarketCode,
  token: string | null,
  loc: Pick<Location, "protocol" | "host"> = window.location,
): string {
  const params = new URLSearchParams();
  if (market === "US") params.set("market", "US");
  if (token) params.set("token", token);
  const query = params.toString();
  const proto = loc.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${loc.host}/ws/events${query ? `?${query}` : ""}`;
}
