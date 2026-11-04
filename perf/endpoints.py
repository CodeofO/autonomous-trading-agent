"""Shared endpoint catalogue for the perf scripts (live + in-process).

Keep keys stable: BEFORE/AFTER JSON files are compared by `key`.
Query params mirror what the frontend actually sends (grep of frontend/src at baseline time).

source:
  "db"          - handler only touches the DB / in-memory state
  "toss_cached" - handler may call the Toss Open API, but behind an in-process cache (TTL noted)
  "toss_live"   - handler calls Toss on (almost) every request
"""

from __future__ import annotations

# (key, path, source, note)
KR_ENDPOINTS: list[tuple[str, str, str, str]] = [
    ("overview", "/api/overview", "toss_cached", "FX 60s module cache + portfolio() per market (quotes 5s cache)"),
    ("markets", "/api/markets", "db", "kv reads + calendar math for each market"),
    ("system", "/api/system", "db", "polled every 5s per market by Layout"),
    ("portfolio", "/api/portfolio", "toss_cached", "PaperBroker quotes via Toss prices (5s cache)"),
    ("agent_structure", "/api/agent/structure", "db", "polled every 5s on Agent page"),
    ("chat", "/api/chat?limit=200", "db", "events.tsx initial load"),
    ("events", "/api/events?limit=200", "db", "events.tsx initial load"),
    ("orders", "/api/orders?limit=300", "db", "Portfolio page"),
    ("fills", "/api/fills?limit=200", "db", "Portfolio page"),
    ("equity", "/api/equity?limit=1000", "db", "Dashboard chart"),
    ("agent_runs", "/api/agent/runs?limit=20", "db", "TradingRoom"),
    ("agent_decisions", "/api/agent/decisions?limit=100", "db", "Portfolio page (extra)"),
    ("agent_progress", "/api/agent/progress", "db", "in-memory only (extra)"),
    ("llm_profile", "/api/llm/profile", "db", "kv reads; OpenRouter model list from in-process cache"),
    ("llm_costs", "/api/llm/costs?months=12", "toss_cached", "llm_usage scan + indicators snapshot (60s cache)"),
    ("watchlist", "/api/watchlist", "db", "1 query per watchlist row (last_bar)"),
    ("market_universe", "/api/market/universe?limit=40", "toss_cached", "Toss stocks_all, 20h cache"),
    ("market_indicators", "/api/market/indicators", "toss_cached", "6 indicators, ~9 Toss calls per refresh, 60s cache"),
    ("reports", "/api/reports", "db", "daily_reports payloads (limit 30)"),
    ("schedule", "/api/schedule", "db", "scheduler.view(): kv reads + calendar math"),
    ("memory_cards", "/api/memory/cards", "db", "all memory cards"),
]

# US stack: same handlers under /api/us (global paths are not duplicated).
US_ENDPOINTS: list[tuple[str, str, str, str]] = [
    ("us_system", "/api/us/system", "db", ""),
    ("us_portfolio", "/api/us/portfolio", "toss_cached", "quotes 5s cache"),
    ("us_agent_structure", "/api/us/agent/structure", "db", ""),
    ("us_chat", "/api/us/chat?limit=200", "db", ""),
    ("us_events", "/api/us/events?limit=200", "db", ""),
    ("us_orders", "/api/us/orders?limit=300", "db", ""),
    ("us_fills", "/api/us/fills?limit=200", "db", ""),
    ("us_equity", "/api/us/equity?limit=1000", "db", ""),
    ("us_agent_runs", "/api/us/agent/runs?limit=20", "db", ""),
    ("us_agent_decisions", "/api/us/agent/decisions?limit=100", "db", "(extra)"),
    ("us_agent_progress", "/api/us/agent/progress", "db", "(extra)"),
    ("us_llm_profile", "/api/us/llm/profile", "db", ""),
    ("us_watchlist", "/api/us/watchlist", "db", ""),
    ("us_market_universe", "/api/us/market/universe?limit=40", "toss_cached", "20h cache"),
    ("us_reports", "/api/us/reports", "db", ""),
    ("us_schedule", "/api/us/schedule", "db", ""),
    ("us_memory_cards", "/api/us/memory/cards", "db", ""),
]

ALL_ENDPOINTS = KR_ENDPOINTS + US_ENDPOINTS


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank-with-interpolation percentile (same as numpy 'linear')."""
    if not values:
        return float("nan")
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    k = (len(xs) - 1) * p / 100.0
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def count_items(body: object) -> int | None:
    """Rough 'rows returned': list length, or the length of the largest list inside a dict."""
    if isinstance(body, list):
        return len(body)
    if isinstance(body, dict):
        lens = [len(v) for v in body.values() if isinstance(v, list)]
        return max(lens) if lens else None
    return None
