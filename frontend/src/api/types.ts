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

export interface LlmProfiles {
  active: LlmProfileName;
  profiles: Record<LlmProfileName, LlmProfileInfo>;
}

export interface SystemInfo {
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
  sources: { name: string; ready: boolean }[];
  tools: { name: string; description: string; group: ToolGroup; source: string; ready: boolean }[];
  schedule: {
    interval_minutes: number;
    morning: string;
    sessions: string[];
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
