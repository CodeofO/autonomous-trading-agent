export type Role = "analyst" | "risk" | "broker" | "system" | "user";
export type Kind = "text" | "tool" | "decision" | "fill" | "block" | "summary";

export interface ChatMsg {
  id: number;
  ts: string;
  role: Role;
  kind: Kind;
  content: string;
  run_id: number | null;
  payload: Record<string, unknown>;
}

// 판단 세션·채팅 답장 진행 상황 (서버가 기준, 새로고침해도 그대로 복원)
export interface AgentProgress {
  active: boolean;
  run_id?: number;
  trigger?: string;
  mode?: string;
  started_at?: string;
  phase?: "start" | "thinking" | "tool" | "executing";
  label?: string;
  step?: number;
  max_steps?: number;
  tools?: number;
  sources?: Record<string, number>;
  tokens?: number;
  tool?: string | null;
  source?: string | null;
  typing: string | null;
  replying: boolean;
}

export interface Position {
  symbol: string;
  name: string;
  quantity: number;
  avg_price: number;
  last_price: number;
  price_day: string | null;
  market_value: number;
  unrealized_pnl: number;
  pnl_pct: number;
  weight_pct: number;
  holding_days: number | null;
}

export interface Portfolio {
  as_of: string;
  latest_price_day: string | null;
  order_window: string;
  engine_status: string;
  cash: number;
  available_cash: number;
  equity: number;
  initial_cash: number;
  total_return_pct: number;
  daily_pnl_pct: number;
  realized_pnl: number;
  fees_paid: number;
  taxes_paid: number;
  positions: Position[];
  open_orders: { id: number; symbol: string; side: string; quantity: number; status: string }[];
  risk_limits: RiskConfig;
  market?: MarketCode;
  currency?: Currency;
}

// LLM 무료/유료 전환 (GET·PUT /api/llm/profile) — 무료 = .env 의 LLM_TEST_*, 유료 = LLM_*
export type LlmProfileName = "free" | "paid";

export interface LlmProfileInfo {
  label: string;
  /** .env 에서 채울 키 묶음 (예: "LLM_TEST_*") */
  env: string;
  configured: boolean;
  model: string | null;
  api: "chat" | "responses" | null;
  reasoning_effort: string | null;
}

// 유료 모델 고르기 (GET /api/llm/profile 의 profiles.paid.models, PUT /api/llm/model) — LlmProvider 는 비용 예상 쪽 정의를 같이 쓴다

/** 유료 모델 목록의 제공사 — "openrouter" = OpenRouter 목록, "env" = 카탈로그에 없는 .env 의 LLM_MODEL */
export type LlmModelProvider = LlmProvider | "openrouter" | "env";

export interface LlmModelOption {
  id: string;
  name: string;
  provider: LlmModelProvider;
  /** OpenRouter 목록만: 모델을 만든 곳 ("anthropic" | "openai" | "deepseek" | …) — 없으면 provider 로 묶는다 */
  group?: string;
  /** OpenRouter 목록만: 묶음 이름 ("Anthropic" | …) */
  group_label?: string;
  /** "최상위" | "주력" | "중간" | "경량" | "" */
  tier: string;
  /** USD / 100만 토큰 — 모르면 null */
  input: number | null;
  output: number | null;
  /** OpenRouter 목록만: 문맥 길이(토큰) */
  context_length?: number | null;
  /** "" | "무료" | ".env" | ".env · 무료" … */
  note: string;
  available: boolean;
  /** 못 쓰는 이유 (예: "OpenAI 키가 .env 에 없어요") */
  reason: string | null;
  selected: boolean;
}

/** 유료 설정 — 모델 고르기 필드는 예전 서버에는 없을 수 있다 */
export interface LlmPaidProfileInfo extends LlmProfileInfo {
  /** .env 키·주소로 알아낸 제공사. null = 알 수 없는 엔드포인트 */
  provider?: LlmProvider | "openrouter" | null;
  provider_label?: string | null;
  /** .env 의 LLM_MODEL (기본값) */
  env_model?: string | null;
  models?: LlmModelOption[];
  /** "openrouter" = OpenRouter 에서 받아 온 목록(POST /api/llm/openrouter/models/refresh), "catalog" = 고정 목록 */
  models_source?: "openrouter" | "catalog";
  /** 목록을 한 번이라도 받아 왔는지 (catalog 는 늘 true) */
  models_loaded?: boolean;
  /** 비었거나 6시간 넘게 지난 목록 (catalog 는 늘 false) */
  models_stale?: boolean;
  models_fetched_at?: string | null;
  /** 마지막으로 목록을 받다 난 오류 */
  models_error?: string | null;
}

export interface LlmProfiles {
  active: LlmProfileName;
  profiles: { free: LlmProfileInfo; paid: LlmPaidProfileInfo };
  /** 미장 매니저 설정(GET·PUT /api/us/llm/profile)이면 "US" */
  market?: MarketCode;
}

// 테스트용(무료) LLM 지정 (GET·PUT·DELETE /api/llm/test-endpoint) — 키는 .env 에만 있고 화면에서 받지 않는다
export type LlmTestApi = "auto" | "chat" | "responses";
export type LlmTestField = "base_url" | "model" | "api" | "reasoning_effort";
/** "화면" = 앱에서 지정해 저장된 값, ".env" = .env 의 LLM_TEST_* */
export type LlmTestSource = "화면" | ".env";

export interface LlmTestValues {
  base_url: string;
  model: string;
  api: LlmTestApi;
  reasoning_effort: string;
}

export interface LlmTestEndpoint extends LlmTestValues {
  source: Record<LlmTestField, LlmTestSource>;
  env: LlmTestValues;
  has_key: boolean;
  configured: boolean;
}

/** POST /api/llm/test-endpoint/check — 무료 엔드포인트에만 짧은 요청 4개 */
export interface LlmTestCheck {
  ok: boolean;
  model: string;
  api: string;
  steps: { label: string; ok: boolean; detail: string }[];
}

export interface SystemInfo {
  /** 미장 스택에만 (국장은 예전 서버에 없을 수 있다) */
  market?: MarketCode;
  currency?: Currency;
  trading_mode: string;
  data_source: string;
  toss_ok: boolean;
  toss_issue: string | null;
  now: string;
  agent_interval_minutes: number;
  llm: { configured: boolean; model: string | null; profile: LlmProfileName; tool_mode: string };
  research: { financials: string; news: string };
  engine_status: "RUNNING" | "PAUSED" | "KILLED";
  agent_running: boolean;
  order_window: string;
  latest_price_day: string | null;
  next_runs: { name: string; at: string }[];
  persona: { name: string; emoji: string };
  disclaimer: string;
}

export interface Order {
  id: number;
  symbol: string;
  name?: string;
  side: "BUY" | "SELL";
  order_type: string;
  quantity: number;
  limit_price: number | null;
  status: string;
  filled_qty: number;
  avg_fill_price: number | null;
  reason: string;
  reject_reason: string | null;
  fill_basis: string;
  trade_day: string | null;
  run_id: number | null;
  created_at: string;
}

export interface FillRow {
  id: number;
  order_id: number;
  symbol: string;
  name: string;
  side: string;
  quantity: number;
  price: number;
  fee: number;
  tax: number;
  filled_at: string;
}

export interface RiskConfig {
  max_position_weight: number;
  max_order_amount: number;
  daily_loss_limit: number;
  stop_loss: number;
  take_profit: number;
  min_order_interval_minutes: number;
  max_positions: number;
  min_cash_ratio: number;
  allow_new_buys: boolean;
}

export interface AgentRun {
  id: number;
  trigger: string;
  mode: string;
  model: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  summary: string;
  market_view: string;
  error: string | null;
  usage: Record<string, number>;
}

export interface Decision {
  id: number;
  run_id: number;
  symbol: string;
  name: string;
  action: "BUY" | "SELL" | "HOLD";
  quantity: number;
  target_weight: number | null;
  confidence: number;
  rationale: string;
  evidence: string[];
  order_id: number | null;
  outcome: string;
  created_at: string;
}

export interface Strategy {
  id: number;
  name: string;
  type: string;
  label: string;
  enabled: boolean;
  params: Record<string, number>;
  symbols: string[];
  schema: { properties: Record<string, { minimum?: number; maximum?: number; default?: number }> };
}

export interface AuditEvent {
  id: number;
  ts: string;
  type: string;
  symbol: string | null;
  message: string;
  payload: Record<string, unknown>;
}

export interface Bar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Marker {
  time: string;
  side: string;
  kind: "fill" | "decision";
  text: string;
}

export interface BacktestResult {
  start: string;
  end: string;
  initial_cash: number;
  final_equity: number;
  total_return_pct: number;
  mdd_pct: number;
  win_rate_pct: number | null;
  trades: number;
  round_trips: number;
  risk_blocks: number;
  equity_curve: { day: string; equity: number }[];
  trades_list: { symbol: string; side: string; quantity: number; price: number; day: string }[];
}

export type ToolGroup = "portfolio" | "market" | "research" | "decision";

export interface AgentStructure {
  persona: { name: string; emoji: string };
  llm: {
    configured: boolean;
    model: string | null;
    profile: LlmProfileName;
    mode: string;
    max_steps: number;
    max_tokens: number;
  };
  sources: { name: string; ready: boolean; note?: string | null }[];
  tools: { name: string; description: string; group: ToolGroup; source: string; ready: boolean }[];
  schedule: {
    interval_minutes: number;
    /** 그날 첫 판단 (예전 화면은 이것을 '장전'으로 보여 줬다) */
    morning: string;
    /** morning 을 뺀 나머지 판단 시각 */
    sessions: string[];
    /** 그날 판단 시각 전부 (한국 시각, 시간 순) — 예전 서버에는 없다 */
    times?: string[];
    custom?: boolean;
    day?: string;
    timezone?: string;
    report: string;
    monitor_minutes: number;
    refresh_minutes: number;
  };
  risk: RiskConfig;
  paper: { initial_cash: number; commission_rate: number; sell_tax_rate: number; slippage_bps: number };
  trading_mode: "paper" | "live";
  live_daily_buy_limit: number | null;
  engine_status: "RUNNING" | "PAUSED" | "KILLED";
  agent_running: boolean;
  /** 매니저가 판단·답장 때 다시 읽어 넣는 '기억' — 예전 서버에는 없다 */
  memory?: AgentMemory;
}

export interface AgentMemory {
  /** 무엇을 어디에 넣는지 (최근 판단 세션 / 투자자 메모 / 오늘 대화 / 계좌·매매 내역) */
  rules: { name: string; where: string; detail: string }[];
  /** at: 서버가 한국 시각 "MM/DD HH:MM" 으로 준다 */
  sessions: { run_id: number; at: string; summary: string; actions: string }[];
  memos: { id: number; at: string; text: string }[];
  /** 최근 대화 메모를 기억에 넣는지 (메모 화면 '기억 설정') — 예전 서버에는 없다 */
  use_recent_messages?: boolean;
  /** 지금 켜져 있는(만료 안 된) 메모 카드 — 예전 서버에는 없다 */
  cards?: { kind: MemoKind; symbol: string | null; name: string | null; text: string }[];
}

export interface PushPrefs {
  fills: boolean;
  risk: boolean;
  alerts: boolean;
  agent: boolean;
}

export interface PushDevice {
  id: string;
  label: string;
  created_at: string;
  last_ok: string | null;
}

export interface PushStatus {
  available: boolean;
  reason: string | null;
  public_key: string | null;
  devices: PushDevice[];
  prefs: PushPrefs;
}

export interface PushSubscribeResult {
  id: string;
  devices: number;
}

export interface PushTestResult {
  sent: number;
  failed: number;
  removed: number;
}

// 대시보드 '주요 지표' (GET /api/market/indicators, 서버 60초 캐시)
export type IndicatorKind = "index" | "etf" | "fx" | "bond";
export type IndicatorUnit = "pt" | "$" | "원" | "%";

export interface Indicator {
  key: string;
  name: string;
  kind: IndicatorKind;
  unit: IndicatorUnit;
  /** 보조 설명 (예: "QQQ ETF" — 지수 대신 ETF 로 대신 보여준다는 표시) */
  note: string | null;
  price: number | null;
  prev_close: number | null;
  /** 전일 대비 — 채권은 %p, 환율은 없음(null) */
  change: number | null;
  change_pct: number | null;
  /** 환율처럼 숫자 없이 방향만 주는 지표 */
  direction: "up" | "down" | null;
  /** 최근 값들 (오래된 것 → 최신) */
  spark: number[];
  error: boolean;
}

export interface MarketIndicators {
  as_of: string;
  items: Indicator[];
}

// LLM 비용 예상 (GET /api/llm/costs?months=12) — 같은 토큰 사용량을 상용 모델 요금으로 환산
export type LlmProvider = "anthropic" | "openai" | "google";
/** 캐시 적용(추정) / 캐시 없음 */
export type LlmScenario = "cache" | "nocache";
export type LlmScenarioCost = Record<LlmScenario, number>;

export interface LlmUsageMonth {
  /** "2026-11" */
  month: string;
  calls: number;
  /** 입력 토큰 전체 (cached 포함) */
  input: number;
  /** 캐시 적중으로 추정한 입력 토큰 (input 의 일부) */
  cached: number;
  output: number;
  sessions: number;
  chats: number;
  /** 예전 세션 합계에서 가져온 기록 수 (호출별 정보 없음) */
  backfill: number;
}

export interface LlmModelPrice {
  /** USD / 100만 토큰 (오늘 기준) */
  input: number;
  cached: number;
  output: number;
  cache_write: number | null;
  /** 호출 1회 입력이 이 토큰 수를 넘으면 장문 요금 */
  long_from: number | null;
}

export interface LlmModelCost {
  id: string;
  provider: LlmProvider;
  name: string;
  tier: string;
  note: string;
  price: LlmModelPrice;
  /** 월("2026-11") → USD */
  months: Record<string, LlmScenarioCost>;
  /** 이번 달 예상 USD (지금까지 + 하루 평균 × 남은 거래일) */
  projection: LlmScenarioCost;
  daily_avg: LlmScenarioCost;
  /** 판단 세션 1회 평균 USD (최근 30일) */
  per_session?: LlmScenarioCost;
}

export interface LlmCosts {
  checked: string;
  sources: Partial<Record<LlmProvider, string>>;
  current_model: string | null;
  /** 지금 쓰는 LLM 설정 (무료/유료) */
  profile: LlmProfileName;
  usd_krw: number | null;
  /** 오래된 달 → 이번 달 */
  months: LlmUsageMonth[];
  models: LlmModelCost[];
  projection: {
    month: string;
    remaining_trading_days: number;
    basis_days: number;
    basis_window_days: number;
    /** schedule: 세션당 평균 × 지금 일정의 하루 세션 수 / history: 최근 사용한 날의 하루 평균 */
    method?: "schedule" | "history";
    sessions_per_day?: number | null;
    basis_sessions?: number;
  };
}

// ---------- 메모 카드 (GET·POST /api/memory/cards, PATCH·DELETE /api/memory/cards/{id}) ----------
/** watch 관심 종목 · focus 주목할 종목 · avoid 피할 종목 · note 운용 지시(종목 없음) */
export type MemoKind = "watch" | "focus" | "avoid" | "note";

export interface MemoCard {
  id: number;
  kind: MemoKind;
  symbol: string | null;
  name: string | null;
  text: string;
  active: boolean;
  /** "YYYY-MM-DD" (한국 날짜, 그날까지 유효) · null = 만료 없음 */
  expires_on: string | null;
  expired: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoSettings {
  /** 트레이딩 룸 최근 대화도 기억에 넣을지 (예전 방식) */
  use_recent_messages: boolean;
  recent_messages: number;
}

export interface MemoCards {
  /** 만료를 판단하는 오늘 날짜 "YYYY-MM-DD" (시장 날짜 — 미장은 뉴욕 날짜). 예전 서버에는 없다 */
  today?: string;
  cards: MemoCard[];
  settings: MemoSettings;
}

export interface MemoCardCreate {
  kind: MemoKind;
  symbol?: string;
  text?: string;
  active?: boolean;
  expires_on?: string | null;
}

export type MemoCardPatch = Partial<Pick<MemoCard, "kind" | "text" | "active" | "expires_on">>;

/** GET /api/market/search?q=…&limit=20 */
export interface StockSearchItem {
  symbol: string;
  name: string;
  market: string;
}

// ---------- 시장 (국장 KR · 미장 US) ----------
export type MarketCode = "KR" | "US";
/** 화면 위 전환: 전체(한눈에) · 국장 · 미장 */
export type MarketView = "ALL" | MarketCode;
export type Currency = "KRW" | "USD";
export type EngineStatus = "RUNNING" | "PAUSED" | "KILLED";

/** 오늘(또는 다음) 정규장 — 모두 KST ISO 시각 */
export interface MarketSession {
  day: string;
  open: string;
  close: string;
  order_cutoff: string;
}

/** GET /api/markets */
export interface MarketInfo {
  code: MarketCode;
  name: string;
  currency: Currency;
  timezone: string;
  enabled: boolean;
  paper_only: boolean;
  trading_mode: string;
  llm_profile: LlmProfileName;
  llm_model: string | null;
  agent_running: boolean;
  engine_status: EngineStatus;
  open_now: boolean;
  session: MarketSession | null;
  next_session_open: string | null;
}

export interface OverviewPosition {
  symbol: string;
  name: string;
  /** 비중(%) */
  weight: number;
  pnl_pct: number;
}

export interface OverviewMarket {
  code: MarketCode;
  name: string;
  currency: Currency;
  equity: number;
  cash: number;
  invested: number;
  pnl_today: number;
  pnl_today_pct: number;
  total_return_pct: number;
  positions: number;
  top_positions: OverviewPosition[];
  engine_status: EngineStatus;
  agent_running: boolean;
  next_run: { name: string; at: string } | null;
  last_session: { run_id: number; at: string; status: string; summary: string } | null;
}

/** GET /api/overview — 두 시장을 원화로 합친 한눈에 보기 */
export interface Overview {
  fx: { usdkrw: number | null; at: string | null };
  markets: OverviewMarket[];
  total_krw: { equity: number | null; pnl_today: number | null };
}

// ---------- 판단 주기 (GET·PUT·DELETE /api/schedule, 미장 /api/us/schedule) ----------

export interface ScheduleInfo {
  market: MarketCode;
  timezone: string;
  /** "한국 시각" · "미국 동부 시각" */
  tz_label: string;
  /** true = 화면에서 정한 값, false = .env 기본값 */
  custom: boolean;
  /** "HH:MM" (시장 시각) */
  start: string;
  end: string;
  interval_minutes: number;
  /** 고를 수 있는 주기(분) */
  intervals: number[];
  /** 하루 판단 시각 "HH:MM" (시장 시각) */
  times: string[];
  /** 같은 세션들의 한국 시각 "HH:MM" */
  times_kst: string[];
  /** 시장 시각 */
  market_hours: { open: string; close: string; order_cutoff: string };
  /** times 중 주문을 낼 수 없는 시각 */
  outside_market: string[];
  report_at: string;
  next_runs: { name: string; at: string }[];
  /** '기본값으로'가 되돌릴 값 (.env 기본 일정) — 예전 서버에는 없다 */
  default?: ScheduleUpdate;
  /** 하루 판단 세션 상한 */
  max_sessions?: number;
  /** 시각을 계산한 거래일 "YYYY-MM-DD" (시장 날짜) */
  day?: string;
  /** 켠 장외 세션과 그 안의 판단 시각 (D-61). times 는 위 일정과 겹치지 않는 것만 — 예전 서버에는 없다 */
  extended?: ScheduleExtSession[];
  /** 장외 세션 판단은 세션 끝 몇 분 전까지 */
  ext_cutoff_minutes?: number;
  /** 일정 범위 + 장외 세션을 합친 하루 판단 수 */
  total?: number;
  /** times 중 켠 장외 세션 안에 드는 시각 (그 시간에도 주문을 받는다) */
  in_extended?: string[];
}

export interface ScheduleExtSession {
  key: string;
  label: string;
  /** 세션 시각 "HH:MM" (시장 시각 / 한국 시각) — 미장 데이마켓은 자정을 넘는다 */
  start: string;
  end: string;
  start_kst: string;
  end_kst: string;
  times: string[];
  times_kst: string[];
}

export interface ScheduleUpdate {
  start: string;
  end: string;
  interval_minutes: number;
}

// ---------- 뉴스 룸 (GET /api/news-room, D-56 — 시장과 관계없는 공용 화면) ----------

export interface NewsItem {
  title: string;
  description: string;
  link: string;
  /** 기사 주소의 도메인 (매체 구분용) */
  domain: string;
  /** 한국 시각 ISO (네이버 pubDate). 모르면 null */
  published_at: string | null;
  /** 같은 사건을 다룬 기사 수 (이 기사 포함) */
  related: number;
}

export interface NewsSection {
  key: string;
  title: string;
  icon: string;
  /** LLM 요약 2~3문장 (요약을 끄거나 LLM 이 없거나 실패하면 null) */
  summary: string | null;
  items: NewsItem[];
  /** 실패한 검색어 안내 */
  errors: string[];
  /** 증시 영향 3줄 (D-59) — 판을 만들 때 함께 저장. 예전 서버에는 없다 */
  impact?: NewsImpact;
}

export interface NewsImpactLine {
  text: string;
  /** 근거 기사 원문 주소 (모르면 null) */
  link: string | null;
  /** 근거 기사 제목 (모르면 null) */
  title: string | null;
}

export interface NewsImpact {
  /** ai = 활성 LLM 이 고른 3줄, rule = LLM 없이 기사 제목으로 대신 ('AI 요약 아님') */
  source: "ai" | "rule";
  lines: NewsImpactLine[];
}

export interface NewsEdition {
  id: number;
  /** 만든 시각 (한국 시각 ISO) */
  created_at: string;
  trigger: "schedule" | "manual";
  /** 정기 갱신 슬롯 "07:00" · "12:00" · "17:00" (수동이면 null) */
  slot: string | null;
  /** '오늘의 흐름' 한 문장 (요약이 없으면 null) */
  headline: string | null;
  summary_model: string | null;
  summary_error: string | null;
  sections: NewsSection[];
  stats: { queries: number; failed_queries: number; articles: number };
}

export interface NewsRoom {
  edition: NewsEdition | null;
  updated_at: string | null;
  /** 다음 자동 갱신 시각 (한국 시각 ISO) */
  next_update_at: string;
  /** ["07:00", "12:00", "17:00"] */
  schedule: string[];
  timezone: string;
  /** 자동 갱신이 켜져 있는지 (스케줄러·뉴스 키) */
  auto: boolean;
  /** 뉴스 검색 키가 있는지 */
  available: boolean;
  /** 쓸 수 없는 이유 (키 없음·키 인증 실패) */
  reason: string | null;
  /** 지금 모으는 중 */
  refreshing: boolean;
  /** 마지막 시도가 실패했으면 그 이유 (이전 판은 그대로 보인다) */
  last_error: string | null;
  last_attempt: string | null;
  settings: { summary: boolean };
  /** 요약에 쓸 지금 활성 LLM */
  llm: { configured: boolean; model: string | null };
  sections: { key: string; title: string; icon: string }[];
}

// ---------- 장외 주문 (GET·PUT /api/extended-hours, 미장 /api/us/extended-hours, D-57) ----------

export type ExtendedSessionKey = "day_market" | "pre_market" | "after_market";

export interface ExtendedSession {
  key: ExtendedSessionKey;
  /** 데이마켓 · 프리마켓 · 애프터마켓 */
  label: string;
  hint: string;
  enabled: boolean;
  /** 지금 이 세션이 진행 중인지 */
  active: boolean;
  /** 다음(또는 지금) 세션 — 토스 장 운영 캘린더 기준. 모르면 null */
  next: {
    day: string;
    /** 한국 시각 ISO */
    start: string;
    end: string;
    /** 시장 현지 시각 HH:MM */
    start_local: string;
    end_local: string;
  } | null;
}

export interface ExtendedHoursInfo {
  market: MarketCode;
  timezone: string;
  live: boolean;
  /** 토스 장 운영 캘린더에서 장외 시각을 받았는지 (못 받으면 장외 주문을 받지 않는다) */
  calendar_known: boolean;
  sessions: ExtendedSession[];
  /** 주문 접수 시간 안내 */
  order_hours: string;
}

export type ExtendedHoursUpdate = Partial<Record<ExtendedSessionKey, boolean>>;

// ---------- 전략 실험실 (GET /api/lab, 미장 /api/us/lab — D-62) ----------

export interface LabStyle {
  id: string;
  name: string;
  emoji: string;
  persona: string;
  summary: string;
  tags: string[];
  /** 매니저 프롬프트에 들어가는 판단 원칙 (기본 매니저는 null — 설정 > 매니저의 투자 정책) */
  policy: string | null;
}

export interface LabRow {
  id: string;
  /** main = 주 계좌(실거래 가능), scenario = 전략 시나리오(모의 전용) */
  kind: "main" | "scenario";
  name: string;
  emoji: string;
  persona: string;
  summary: string;
  live: boolean;
  equity?: number;
  cash?: number;
  initial_cash?: number | null;
  return_pct?: number | null;
  positions?: number;
  top_positions?: { symbol: string; name: string; weight: number; pnl_pct: number }[];
  invested_pct?: number;
  fills?: number;
  runs?: number;
  since?: string | null;
  last_run?: { id: number; at: string | null; status: string; summary: string } | null;
  running: boolean;
  /** 계좌를 읽지 못했을 때 */
  error?: string;
}

export interface LabView {
  market: MarketCode;
  currency: Currency;
  /** 주 계좌가 실거래 계좌인지 */
  live: boolean;
  main: string;
  enabled: string[];
  max_scenarios: number;
  styles: LabStyle[];
  rows: LabRow[];
  round_running: boolean;
  running: string | null;
  llm: string | null;
  next_run: { name: string; at: string } | null;
}

export interface LabDetail {
  id: string;
  positions: {
    symbol: string;
    name: string;
    quantity: number;
    avg_price: number;
    last_price: number;
    market_value: number;
    pnl_pct: number;
  }[];
  orders: {
    id: number;
    symbol: string;
    name: string;
    side: string;
    quantity: number;
    status: string;
    filled_qty: number;
    avg_fill_price: number | null;
    created_at: string | null;
    reason: string | null;
  }[];
  runs: {
    id: number;
    trigger: string;
    status: string;
    started_at: string | null;
    summary: string;
    market_view: string;
  }[];
  equity: { ts: string | null; equity: number }[];
}
