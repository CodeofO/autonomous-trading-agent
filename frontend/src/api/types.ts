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

export interface SystemInfo {
  trading_mode: string;
  data_source: string;
  toss_ok: boolean;
  toss_issue: string | null;
  now: string;
  agent_interval_minutes: number;
  llm: { configured: boolean; model: string | null; host: string | null; tool_mode: string };
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
