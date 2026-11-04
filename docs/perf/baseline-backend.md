# Backend performance baseline (BEFORE)

Generated 2026-11-04T14:08:22+00:00 from git `80b1f74` on `main` (100 uncommitted paths in the working tree). Re-run with the commands in `perf/README.md`; compare runs with `python3 perf/compare.py`.

## Headline numbers

- live: slowest p50: **orders 23.99 ms**
- live: largest payload: **events 141,380 B (32,889 B gzipped)**
- live: median of endpoint p50s: **9.22 ms**
- in-process: most SQL per request: **orders 302 statements**
- in-process: total SQL across all endpoints: **1233**
- in-process: slowest median: **llm_costs 91.639 ms**
- agent session (25 tool calls): **1163 SQL, 62 Toss calls, 235,759 B sent to the LLM**
- live KR DB size: **15.4 MB, 13 unindexed filter columns**
- code: **12,699 lines, C901>10: 15, tests 304 passed in 22.28s**

## 1. Live API latency (docker stack, GET only)

15 sequential requests per endpoint after 2 warm-ups, <= 5 req/s, keep-alive, taken 2026-11-04T13:57:20.577854+00:00 (server state at start: {"KR": {"enabled": true, "open_now": false, "agent_running": false, "engine_status": "RUNNING"}, "US": {"enabled": true, "open_now": false, "agent_running": false, "engine_status": "RUNNING"}}). `bytes_gzip` = size if the response were gzip-compressed (nothing compresses API responses today).

**Calls Toss behind a cache**

| key | path | p50 ms | p95 ms | max ms | bytes | gzip | items |
|---|---|---|---|---|---|---|---|
| overview | /api/overview | 23.0 | 27.4 | 30.2 | 1,609 | 915 | 2 |
| portfolio | /api/portfolio | 16.9 | 25.2 | 25.3 | 1,818 | 858 | 5 |
| llm_costs | /api/llm/costs?months=12 | 16.1 | 28.1 | 51.0 | 11,452 | 1,554 | 12 |
| market_universe | /api/market/universe?limit=40 | 4.8 | 7.1 | 7.6 | 2,373 | 572 | 40 |
| market_indicators | /api/market/indicators | 4.6 | 7.0 | 8.6 | 2,186 | 992 | 6 |
| us_portfolio | /api/us/portfolio | 7.6 | 10.8 | 11.3 | 1,095 | 688 | 9 |
| us_market_universe | /api/us/market/universe?limit=40 | 5.7 | 6.7 | 6.7 | 2,499 | 625 | 40 |

**DB / memory only**

| key | path | p50 ms | p95 ms | max ms | bytes | gzip | items |
|---|---|---|---|---|---|---|---|
| markets | /api/markets | 11.7 | 13.3 | 15.2 | 933 | 360 | 2 |
| system | /api/system | 13.7 | 15.1 | 15.5 | 993 | 660 | 4 |
| agent_structure | /api/agent/structure | 21.1 | 26.8 | 29.7 | 7,320 | 3,413 | 13 |
| chat | /api/chat?limit=200 | 15.7 | 26.0 | 41.9 | 105,176 | 23,059 | 200 |
| events | /api/events?limit=200 | 15.7 | 20.1 | 21.5 | 141,380 | 32,889 | 200 |
| orders | /api/orders?limit=300 | 24.0 | 32.1 | 34.8 | 15,107 | 4,759 | 19 |
| fills | /api/fills?limit=200 | 23.9 | 26.6 | 26.9 | 3,414 | 935 | 19 |
| equity | /api/equity?limit=1000 | 15.8 | 18.6 | 19.0 | 43,683 | 5,609 | 515 |
| agent_runs | /api/agent/runs?limit=20 | 10.3 | 13.4 | 14.3 | 26,118 | 7,977 | 20 |
| agent_decisions | /api/agent/decisions?limit=100 | 14.1 | 16.9 | 18.9 | 86,697 | 21,613 | 100 |
| agent_progress | /api/agent/progress | 4.7 | 5.9 | 6.1 | 47 | 60 | - |
| llm_profile | /api/llm/profile | 12.4 | 13.6 | 13.7 | 3,103 | 899 | - |
| watchlist | /api/watchlist | 6.4 | 10.2 | 10.3 | 2 | 22 | 0 |
| reports | /api/reports | 9.7 | 12.2 | 13.2 | 32,876 | 8,635 | 3 |
| schedule | /api/schedule | 8.8 | 12.1 | 14.7 | 784 | 358 | 9 |
| memory_cards | /api/memory/cards | 8.2 | 9.9 | 10.0 | 93 | 95 | 0 |
| us_system | /api/us/system | 9.6 | 11.8 | 14.5 | 1,112 | 737 | 4 |
| us_agent_structure | /api/us/agent/structure | 13.1 | 18.7 | 24.9 | 5,030 | 2,450 | 10 |
| us_chat | /api/us/chat?limit=200 | 6.0 | 7.9 | 8.4 | 358 | 335 | 1 |
| us_events | /api/us/events?limit=200 | 5.6 | 7.0 | 7.0 | 813 | 388 | 2 |
| us_orders | /api/us/orders?limit=300 | 6.6 | 12.7 | 14.4 | 2 | 22 | 0 |
| us_fills | /api/us/fills?limit=200 | 6.2 | 8.7 | 9.3 | 2 | 22 | 0 |
| us_equity | /api/us/equity?limit=1000 | 7.0 | 8.6 | 8.9 | 445 | 142 | 6 |
| us_agent_runs | /api/us/agent/runs?limit=20 | 6.8 | 8.7 | 9.3 | 2 | 22 | 0 |
| us_agent_decisions | /api/us/agent/decisions?limit=100 | 6.9 | 12.8 | 16.3 | 2 | 22 | 0 |
| us_agent_progress | /api/us/agent/progress | 4.6 | 6.1 | 6.5 | 47 | 60 | - |
| us_llm_profile | /api/us/llm/profile | 11.3 | 14.5 | 15.8 | 3,117 | 916 | - |
| us_watchlist | /api/us/watchlist | 9.2 | 12.8 | 13.2 | 804 | 337 | 9 |
| us_reports | /api/us/reports | 5.6 | 6.7 | 7.8 | 2 | 22 | 0 |
| us_schedule | /api/us/schedule | 7.9 | 11.5 | 12.8 | 797 | 385 | 9 |
| us_memory_cards | /api/us/memory/cards | 6.2 | 8.8 | 9.7 | 93 | 95 | 0 |

## 2. In-process deterministic metrics (seeded SQLite, fake Toss)

Seeded rows per stack: daily_prices=10,000, watchlist=20, agent_runs=300, orders=1,000, fills=1,000, agent_decisions=2,100, chat_messages=2,000, audit_events=5,000, equity_snapshots=2,000, llm_usage=3,150, daily_reports=60, memory_cards=20, api_cache=200, paper_state_bytes=363,879. Clock 2026-11-02T10:00:00+09:00. Median of 20 requests. `sql` = statements per request (KR+US engines), `toss` = Toss calls per request at steady state (cold = first call), `dup` = most repeated statement in one request.

| key | median ms | p95 ms | sql | db ms | toss (steady/cold) | bytes | gzip | rows | dup |
|---|---|---|---|---|---|---|---|---|---|
| overview | 7.49 | 7.74 | 52 | 0.65 | 0.0 / 1 | 2,699 | 1,368 | 2 | 16x |
| markets | 1.07 | 1.15 | 4 | 0.02 | 0.0 / 0 | 730 | 335 | 2 | 3x |
| system | 1.25 | 1.34 | 6 | 0.03 | 0.0 / 0 | 964 | 608 | 4 | 5x |
| portfolio | 3.34 | 3.42 | 21 | 0.29 | 0.0 / 0 | 3,831 | 1,136 | 20 | 16x |
| agent_structure | 2.94 | 3.24 | 16 | 0.11 | 0.0 / 0 | 10,619 | 4,796 | 13 | 10x |
| chat | 3.85 | 3.92 | 1 | 0.02 | 0.0 / 0 | 164,244 | 42,161 | 200 |  |
| events | 6.42 | 9.26 | 1 | 0.02 | 0.0 / 0 | 377,129 | 97,788 | 200 |  |
| orders | 42.20 | 43.10 | 302 | 3.26 | 0.0 / 0 | 166,104 | 28,685 | 300 | 300x |
| fills | 29.91 | 31.05 | 202 | 3.01 | 0.0 / 0 | 36,245 | 5,891 | 200 | 200x |
| equity | 5.96 | 28.46 | 2 | 0.02 | 0.0 / 0 | 79,452 | 16,050 | 1,000 |  |
| agent_runs | 1.28 | 2.41 | 2 | 0.03 | 0.0 / 0 | 25,351 | 8,914 | 20 |  |
| agent_decisions | 3.01 | 4.28 | 2 | 0.03 | 0.0 / 0 | 119,396 | 31,732 | 100 |  |
| agent_progress | 0.53 | 0.55 | 0 | 0.00 | 0.0 / 0 | 47 | 60 | - |  |
| llm_profile | 1.25 | 1.48 | 5 | 0.02 | 0.0 / 0 | 3,710 | 905 | - | 5x |
| llm_costs | 91.64 | 109.83 | 3 | 0.03 | 0.0 / 8 | 11,712 | 1,864 | 12 | 2x |
| watchlist | 3.33 | 3.51 | 21 | 0.31 | 0.0 / 0 | 1,852 | 366 | 20 | 20x |
| market_universe | 0.44 | 0.47 | 0 | 0.00 | 0.0 / 2 | 2,423 | 365 | 40 |  |
| market_indicators | 0.43 | 0.44 | 0 | 0.00 | 0.0 / 0 | 2,539 | 889 | 6 |  |
| reports | 9.73 | 9.84 | 1 | 0.02 | 0.0 / 0 | 753,695 | 192,740 | 30 |  |
| schedule | 1.00 | 1.07 | 3 | 0.01 | 0.0 / 0 | 844 | 360 | 9 | 3x |
| memory_cards | 0.97 | 1.05 | 2 | 0.01 | 0.0 / 0 | 7,057 | 2,212 | 20 |  |
| us_system | 1.27 | 1.39 | 6 | 0.03 | 0.0 / 0 | 1,066 | 678 | 4 | 4x |
| us_portfolio | 3.41 | 3.77 | 21 | 0.29 | 0.0 / 1 | 3,994 | 1,298 | 20 | 16x |
| us_agent_structure | 2.92 | 3.02 | 16 | 0.10 | 0.0 / 0 | 9,854 | 4,746 | 10 | 9x |
| us_chat | 3.57 | 3.70 | 1 | 0.02 | 0.0 / 0 | 141,129 | 37,281 | 200 |  |
| us_events | 7.85 | 9.36 | 1 | 0.02 | 0.0 / 0 | 504,857 | 129,676 | 200 |  |
| us_orders | 42.71 | 46.62 | 302 | 3.31 | 0.0 / 0 | 165,879 | 29,098 | 300 | 300x |
| us_fills | 30.72 | 34.74 | 202 | 3.21 | 0.0 / 0 | 36,018 | 5,877 | 200 | 200x |
| us_equity | 6.36 | 31.21 | 2 | 0.03 | 0.0 / 0 | 76,418 | 16,176 | 1,000 |  |
| us_agent_runs | 1.34 | 2.37 | 2 | 0.02 | 0.0 / 0 | 25,360 | 8,058 | 20 |  |
| us_agent_decisions | 3.08 | 5.51 | 2 | 0.03 | 0.0 / 0 | 119,044 | 30,976 | 100 |  |
| us_agent_progress | 0.54 | 0.58 | 0 | 0.00 | 0.0 / 0 | 47 | 60 | - |  |
| us_llm_profile | 1.31 | 2.32 | 5 | 0.03 | 0.0 / 0 | 3,724 | 915 | - | 5x |
| us_watchlist | 3.52 | 4.84 | 21 | 0.33 | 0.0 / 0 | 1,784 | 497 | 20 | 20x |
| us_market_universe | 0.45 | 0.49 | 0 | 0.00 | 0.0 / 3 | 2,424 | 437 | 40 |  |
| us_reports | 9.73 | 9.91 | 1 | 0.02 | 0.0 / 0 | 752,743 | 189,268 | 30 |  |
| us_schedule | 1.04 | 1.11 | 3 | 0.01 | 0.0 / 0 | 858 | 396 | 9 | 3x |
| us_memory_cards | 1.00 | 1.06 | 2 | 0.01 | 0.0 / 0 | 6,934 | 2,348 | 20 |  |

**Background jobs, cost per invocation** (caches reset to emulate a real 1-min / 10-min tick)

| job | median ms | sql | toss calls | toss by api |
|---|---|---|---|---|
| kr_intraday_1min | 3.97 | 15 | 2.0 | {"prices": 2} |
| kr_sync_data_10min | 23.31 | 49 | 23.0 | {"prices": 1, "candles": 22} |
| kr_scheduler_due_1min | 0.38 | 2 | 0.0 | {} |
| indicators_refresh_60s | 1.18 | 0 | 8.0 | {"index_prices": 1, "prices": 1, "fx": 1, "index_candles": 3, "candles": 2} |
| us_intraday_1min | 8.21 | 32 | 2.0 | {"prices": 2} |
| us_sync_data_10min | 39.39 | 53 | 24.0 | {"prices": 1, "candles": 23} |
| us_scheduler_due_1min | 0.38 | 2 | 0.0 | {} |

**Agent session** (scripted fake LLM via respx, KR stack with seeded history)

| metric | LLM session (scripted) | rule-based session |
|---|---|---|
| LLM calls / tool calls | 6 / 25 | - |
| SQL statements | 1163 | 98 |
| Toss calls | 62 | 13 |
| wall ms (LLM client is rate-limited to 2 req/s) | 2,039.2 | 86.3 |
| LLM request bytes per call | 13,463, 26,101, 33,336, 44,810, 58,301, 59,748 | - |
| LLM request bytes total | 235,759 | - |
| rows written chat / audit | 39 / 8 | - |

Most repeated statements in the LLM session:

- 1047x `[kr] SELECT daily_prices.id, daily_prices.symbol, daily_prices.day, daily_prices.name, daily_prices.market, da…`
- 39x `[kr] INSERT INTO chat_messages (ts, run_id, role, kind, content, payload) VALUES (?, ?, ?, ?, ?, ?)…`
- 14x `[kr] SELECT app_settings."key" AS app_settings_key, app_settings.value AS app_settings_value, app_settings.upd…`
- 8x `[kr] INSERT INTO audit_events (ts, type, symbol, message, payload) VALUES (?, ?, ?, ?, ?)…`
- 6x `[kr] INSERT INTO llm_usage (ts, kind, run_id, model, calls, input_tokens, cached_tokens, output_tokens, max_in…`
- 6x `[kr] SELECT count(*) AS count_1 FROM daily_prices WHERE daily_prices.symbol = ? AND daily_prices.day >= ? AND …`

Paper account JSON blob after seeding 1,000 orders: 366,010 bytes (rewritten in full on every order/fill).

## 3. Live database (read-only)

### KR - PostgreSQL 16.15, database 15,047.0 KB

| table | rows | total | heap | indexes | toast | dead tup | seq scans | idx scans |
|---|---|---|---|---|---|---|---|---|
| chat_messages | 2,172 | 1,592.0 KB | 1,312.0 KB | 184.0 KB | 64.0 KB | 0 | 11 | 251 |
| api_cache | 198 | 1,232.0 KB | 48.0 KB | 32.0 KB | 1,128.0 KB | 3 | 169 | 15 |
| daily_prices | 4,523 | 1,224.0 KB | 624.0 KB | 568.0 KB | 0.0 KB | 62 | 4 | 16,020 |
| corp_codes | 4,009 | 768.0 KB | 512.0 KB | 224.0 KB | 0.0 KB | 0 | 35 | 33 |
| agent_decisions | 336 | 600.0 KB | 512.0 KB | 48.0 KB | 8.0 KB | 7 | 6 | 226 |
| audit_events | 536 | 472.0 KB | 128.0 KB | 96.0 KB | 216.0 KB | 0 | 107 | 8 |
| equity_snapshots | 517 | 168.0 KB | 80.0 KB | 64.0 KB | 0.0 KB | 43 | 12 | 189 |
| agent_runs | 51 | 152.0 KB | 104.0 KB | 16.0 KB | 8.0 KB | 5 | 83 | 425 |
| paper_account | 1 | 128.0 KB | 24.0 KB | 16.0 KB | 64.0 KB | 5 | 6 | 26 |
| daily_reports | 3 | 120.0 KB | 16.0 KB | 16.0 KB | 64.0 KB | 0 | 4 | 35 |
| orders | 19 | 112.0 KB | 16.0 KB | 64.0 KB | 8.0 KB | 5 | 432 | 0 |
| llm_usage | 68 | 88.0 KB | 16.0 KB | 48.0 KB | 0.0 KB | 0 | 61 | 36 |
| app_settings | 10 | 64.0 KB | 8.0 KB | 16.0 KB | 8.0 KB | 2 | 11,164 | 0 |
| strategies | 2 | 48.0 KB | 8.0 KB | 32.0 KB | 8.0 KB | 0 | 89 | 0 |
| fills | 19 | 40.0 KB | 8.0 KB | 32.0 KB | 0.0 KB | 0 | 52 | 1 |
| alembic_version | 1 | 24.0 KB | 8.0 KB | 16.0 KB | 0.0 KB | 2 | 18 | 2 |
| memory_cards | 0 | 16.0 KB | 0.0 KB | 8.0 KB | 8.0 KB | 0 | 74 | 0 |
| users | 0 | 16.0 KB | 0.0 KB | 16.0 KB | 0.0 KB | 0 | 134 | 0 |
| watchlist | 0 | 8.0 KB | 0.0 KB | 8.0 KB | 0.0 KB | 0 | 459 | 0 |

**Largest JSON/TEXT columns** (length of the JSON text)

| column | rows | avg | p95 | max | sum |
|---|---|---|---|---|---|
| api_cache.payload | 198 | 49,023 | 189,636 | 229,895 | 9,479.1 KB |
| chat_messages.payload | 2,172 | 427 | 1,511 | 32,177 | 906.3 KB |
| audit_events.payload | 536 | 964 | 10,794 | 12,215 | 504.4 KB |
| agent_decisions.evidence | 336 | 414 | 785 | 1,285 | 135.7 KB |
| chat_messages.content | 2,172 | 42 | 151 | 627 | 89.4 KB |
| agent_decisions.rationale | 336 | 190 | 288 | 451 | 62.4 KB |
| daily_reports.payload | 3 | 17,008 | 32,165 | 32,165 | 49.8 KB |
| agent_runs.summary | 51 | 341 | 569 | 612 | 17.0 KB |
| audit_events.message | 536 | 20 | 70 | 133 | 10.4 KB |
| agent_runs.market_view | 51 | 185 | 295 | 305 | 9.2 KB |

**audit_events by type**

| type | rows | avg payload | max payload |
|---|---|---|---|
| SIGNAL | 336 | 34 | 34 |
| AGENT_RUN | 49 | 117 | 169 |
| AGENT_PAYLOAD | 44 | 10,495 | 12,215 |
| SYSTEM | 26 | 2 | 2 |
| ORDER | 19 | 635 | 884 |
| FILL | 19 | 51 | 52 |
| FILLED | 19 | 1,077 | 1,324 |
| CONFIG | 9 | 79 | 239 |
| RISK_BLOCK | 5 | 87 | 89 |
| REPORT | 3 | 2 | 2 |
| SUBMITTED | 3 | 954 | 1,219 |
| ERROR | 1 | 2 | 2 |
| ENGINE | 1 | 2 | 2 |
| KILL_SWITCH | 1 | 15 | 15 |
| ACCOUNT_RESET | 1 | 2 | 2 |

**chat_messages by role/kind**

| role | kind | rows | avg payload | max payload | avg content |
|---|---|---|---|---|---|
| analyst | tool | 1199 | 337 | 970 | 19 |
| analyst | text | 457 | 3 | 41 | 89 |
| analyst | decision | 336 | 1,320 | 3,052 | 10 |
| system | text | 84 | 14 | 22 | 53 |
| analyst | summary | 44 | 44 | 45 | 395 |
| broker | fill | 19 | 1,170 | 1,419 | 51 |
| user | text | 14 | 2 | 2 | 20 |
| risk | text | 8 | 20 | 20 | 48 |
| risk | block | 5 | 44 | 44 | 66 |
| system | summary | 3 | 17,020 | 32,177 | 76 |
| broker | text | 3 | 974 | 1,239 | 54 |

**Write rate** - equity snapshots per KST day (and how many outside 09-15h): 2026-11-02: 110 (65), 2026-11-03: 212 (103), 2026-11-04: 195 (110); chat messages per day: 2026-11-02: 812, 2026-11-03: 1093, 2026-11-04: 267.

paper_account.state: 8,126 bytes for 19 orders / 5 positions. api_cache: dart 198 rows avg 49,023 chars max 229,895.

**Live activity sample** (60.3 s, server state {"KR": {"open_now": false, "agent_running": false}, "US": {"open_now": false, "agent_running": false}}; includes scheduler jobs and any browser tab that was open): 110.5 commits/min, 3445.5 tuples returned/min, 0.0 inserted/min; per table: daily_prices seq 0.0/idx 11.9/ins 0.0, app_settings seq 72.7/idx 0.0/ins 0.0.

**Indexes**

| table | index | size | scans | definition |
|---|---|---|---|---|
| agent_decisions | agent_decisions_pkey | 16.0 KB | 87 | `btree (id)` |
| agent_decisions | ix_agent_decisions_run_id | 16.0 KB | 135 | `btree (run_id)` |
| agent_decisions | ix_agent_decisions_symbol | 16.0 KB | 4 | `btree (symbol)` |
| agent_runs | agent_runs_pkey | 16.0 KB | 425 | `btree (id)` |
| alembic_version | alembic_version_pkc | 16.0 KB | 2 | `btree (version_num)` |
| api_cache | api_cache_pkey | 32.0 KB | 15 | `btree (key)` |
| app_settings | app_settings_pkey | 16.0 KB | 0 | `btree (key)` |
| audit_events | audit_events_pkey | 32.0 KB | 1 | `btree (id)` |
| audit_events | ix_audit_events_symbol | 16.0 KB | 0 | `btree (symbol)` |
| audit_events | ix_audit_events_ts | 32.0 KB | 5 | `btree (ts)` |
| audit_events | ix_audit_events_type | 16.0 KB | 2 | `btree (type)` |
| chat_messages | chat_messages_pkey | 64.0 KB | 215 | `btree (id)` |
| chat_messages | ix_chat_messages_run_id | 32.0 KB | 0 | `btree (run_id)` |
| chat_messages | ix_chat_messages_ts | 64.0 KB | 36 | `btree (ts)` |
| corp_codes | corp_codes_pkey | 224.0 KB | 33 | `btree (stock_code)` |
| daily_prices | daily_prices_pkey | 120.0 KB | 410 | `btree (id)` |
| daily_prices | ix_daily_prices_day | 56.0 KB | 2,523 | `btree (day)` |
| daily_prices | ix_daily_prices_symbol | 72.0 KB | 348 | `btree (symbol)` |
| daily_prices | uq_daily_price | 224.0 KB | 12,739 | `btree (symbol, day)` |
| daily_reports | daily_reports_pkey | 16.0 KB | 35 | `btree (day)` |
| equity_snapshots | equity_snapshots_pkey | 32.0 KB | 90 | `btree (id)` |
| equity_snapshots | ix_equity_snapshots_ts | 32.0 KB | 99 | `btree (ts)` |
| fills | fills_pkey | 16.0 KB | 1 | `btree (id)` |
| fills | ix_fills_order_id | 16.0 KB | 0 | `btree (order_id)` |
| llm_usage | ix_llm_usage_run_id | 16.0 KB | 0 | `btree (run_id)` |
| llm_usage | ix_llm_usage_ts | 16.0 KB | 36 | `btree (ts)` |
| llm_usage | llm_usage_pkey | 16.0 KB | 0 | `btree (id)` |
| memory_cards | memory_cards_pkey | 8.0 KB | 0 | `btree (id)` |
| orders | ix_orders_client_order_id | 16.0 KB | 0 | `btree (client_order_id)` |
| orders | ix_orders_status | 16.0 KB | 0 | `btree (status)` |
| orders | ix_orders_symbol | 16.0 KB | 0 | `btree (symbol)` |
| orders | orders_pkey | 16.0 KB | 0 | `btree (id)` |
| paper_account | paper_account_pkey | 16.0 KB | 26 | `btree (id)` |
| strategies | strategies_name_key | 16.0 KB | 0 | `btree (name)` |
| strategies | strategies_pkey | 16.0 KB | 0 | `btree (id)` |
| users | users_pkey | 8.0 KB | 0 | `btree (id)` |
| users | users_username_key | 8.0 KB | 0 | `btree (username)` |
| watchlist | watchlist_pkey | 8.0 KB | 0 | `btree (symbol)` |

Redundant: `ix_daily_prices_symbol` (covered by `uq_daily_price`)

**Filtered/ordered columns without an index** (cross-checked against app code)

| table.column | rows now | used in |
|---|---|---|
| orders.created_at | 19 | routes.py:253 orders since-filter; reports/daily.py:26 day range |
| orders.run_id | 19 | container.py:198 purge_legacy; trades.py join |
| orders.broker_order_id | 19 | orders.py:330/341 live order sync |
| fills.filled_at | 19 | routes.py:270 since-filter; trades.py:40 ledger; reports/daily.py:24 |
| fills.symbol | 19 | routes.py:1300 candles markers |
| agent_runs.started_at | 51 | routes.py:434/1529 since-filter; runner.py:290; reports/daily.py:36 |
| agent_runs.status | 51 | runner.py:288 memory_snapshot status='DONE' |
| agent_decisions.created_at | 336 | routes.py:464 since-filter |
| agent_decisions.order_id | 336 | container.py:203 purge_legacy |
| chat_messages.role | 2,172 | runner.py:309/697 role filters (USER/ANALYST) |
| memory_cards.kind | 0 | routes.py:1120 duplicate check |
| memory_cards.symbol | 0 | routes.py:1120 duplicate check |
| api_cache.fetched_at | 198 | (no filter in code; TTL checked in Python) |

**EXPLAIN ANALYZE of hot read queries**

| query | exec ms | rows | buffers hit | plan |
|---|---|---|---|---|
| chat_latest_200 | 0.168 | 200 | 139 | Limit > Index Scan on chat_messages using chat_messages_pkey |
| events_latest_200 | 0.241 | 200 | 19 | Limit > Sort > Seq Scan on audit_events |
| orders_since_300 | 0.077 | 19 | 5 | Limit > Sort > Seq Scan on orders |
| fills_since_200 | 0.061 | 19 | 4 | Limit > Sort > Seq Scan on fills |
| equity_since_1000 | 0.184 | 517 | 51 | Limit > Index Scan on equity_snapshots using equity_snapshots_pkey |
| runs_since_20 | 0.140 | 20 | 16 | Limit > Sort > Seq Scan on agent_runs |
| memory_runs_done_3 | 0.033 | 3 | 3 | Limit > Index Scan on agent_runs using agent_runs_pkey |
| memory_user_msgs_5 | 0.419 | 5 | 1,255 | Limit > Index Scan on chat_messages using chat_messages_pkey |
| last_order_times | 0.031 | 19 | 2 | Seq Scan on orders |
| trade_ledger_all | 0.126 | 19 | 9 | Sort > Hash Join > Seq Scan on fills > Hash > Seq Scan on orders |
| name_of_last_bar | 0.060 | 1 | 4 | Limit > Limit > Seq Scan on daily_prices > Index Scan on daily_prices using uq_daily_price |
| fills_by_symbol | 0.041 | 1 | 2 | Seq Scan on fills > Limit > Seq Scan on fills |
| llm_usage_12mo | 0.032 | 68 | 2 | Seq Scan on llm_usage |

### US - SQLite (wal), files: us_market.db-wal 390.3 KB, us_market.db 392.0 KB, us_market.db-shm 32.0 KB

| table | rows | table bytes | index bytes |
|---|---|---|---|
| agent_runs | 3 | 4,096 | 0 |
| alembic_version | 1 | 4,096 | 4,096 |
| app_settings | 6 | 4,096 | 4,096 |
| audit_events | 5 | 4,096 | 12,288 |
| chat_messages | 7 | 4,096 | 8,192 |
| daily_prices | 1,251 | 139,264 | 86,016 |
| equity_snapshots | 10 | 4,096 | 4,096 |
| paper_account | 1 | 4,096 | 0 |
| strategies | 2 | 4,096 | 4,096 |
| watchlist | 9 | 4,096 | 4,096 |

Same schema as KR, so the same columns lack indexes (13 of the checked filter columns).

## 4. Code metrics (backend/app)

- 67 modules, 12,699 lines (10,921 non-blank/non-comment); tests: 33 files, 6,228 lines
- functions: 668, >50 lines: 33, >100 lines: 5
- ruff C901 (max-complexity 10): **15** functions; ruff default rules: 0 violations
- `# type: ignore`: 2, `except Exception`: 40, `# noqa`: 0, TODO/FIXME: 1, private `engine._health` access: 7
- mypy: Success: no issues found in 67 source files (7.3 s)
- pytest: 304 passed in 22.28s (wall 22.9 s, `-p no:warnings -o addopts=`)

**Largest modules**

| module | lines | code |
|---|---|---|
| app/api/routes.py | 1647 | 1363 |
| app/agent/tools.py | 1024 | 955 |
| app/agent/runner.py | 891 | 814 |
| app/market/toss.py | 575 | 509 |
| app/container.py | 563 | 507 |
| app/brokers/paper.py | 487 | 440 |
| app/engine/scheduler.py | 438 | 365 |
| app/engine/orders.py | 400 | 369 |
| app/brokers/toss_live.py | 397 | 347 |
| app/agent/llm.py | 375 | 326 |
| app/engine/engine.py | 365 | 324 |
| app/agent/costs.py | 320 | 284 |
| app/engine/push.py | 285 | 245 |
| app/db/models.py | 284 | 225 |
| app/agent/profiles.py | 276 | 241 |
| app/research/dart.py | 236 | 207 |
| app/core/config.py | 233 | 170 |
| app/backtest/engine.py | 203 | 179 |
| app/agent/activity.py | 189 | 169 |
| app/core/market_calendar.py | 185 | 155 |

**C901 complexity > 10**

| function | complexity | location |
|---|---|---|
| result_line | 28 | app/agent/activity.py:54 |
| check | 16 | app/risk/rules.py:79 |
| run_backtest | 15 | app/backtest/engine.py:58 |
| preview | 14 | app/agent/activity.py:129 |
| summary | 14 | app/agent/costs.py:192 |
| place_order | 14 | app/brokers/toss_live.py:288 |
| create_app | 14 | app/main.py:35 |
| normalize_decisions | 13 | app/agent/runner.py:817 |
| _run_tools | 12 | app/agent/runner.py:361 |
| place_order | 12 | app/brokers/paper.py:271 |
| chat | 11 | app/agent/llm.py:220 |
| _responses | 11 | app/agent/llm.py:277 |
| rule_based_decisions | 11 | app/agent/rule_agent.py:36 |
| _us_market_overview | 11 | app/agent/tools.py:751 |
| events_ws | 11 | app/api/ws.py:17 |

**Largest functions**

| function | lines | location |
|---|---|---|
| build_container | 152 | app/container.py:292 |
| summary | 129 | app/agent/costs.py:192 |
| _build_us | 118 | app/container.py:446 |
| run_backtest | 116 | app/backtest/engine.py:58 |
| _run_tools | 103 | app/agent/runner.py:361 |
| agent_structure | 95 | app/api/routes.py:684 |
| rule_based_decisions | 93 | app/agent/rule_agent.py:36 |
| _us_spec | 93 | app/agent/tools.py:275 |
| run | 87 | app/agent/runner.py:181 |
| create_app | 82 | app/main.py:35 |
| build_daily_report | 81 | app/reports/daily.py:15 |
| _execute_one | 80 | app/agent/runner.py:591 |
| submit | 80 | app/engine/orders.py:131 |
| trade_ledger | 75 | app/engine/trades.py:21 |
| result_line | 73 | app/agent/activity.py:54 |
| check | 70 | app/agent/llm_check.py:75 |
| t_explore_sector | 69 | app/agent/tools.py:915 |
| _announce | 68 | app/engine/orders.py:251 |
| check | 67 | app/risk/rules.py:79 |
| t_get_price_history | 65 | app/agent/tools.py:527 |

**Duplicate-code hints**: 39 repeated 6-line windows in 9 clusters; identical function bodies: [['app/agent/tools.py:31 _f', 'app/market/toss.py:83 _num']]

- app/container.py:401, app/container.py:521 (9 windows)
- app/agent/tools.py:106, app/agent/tools.py:289 (8 windows)
- app/container.py:423, app/container.py:543 (7 windows)
- app/agent/llm.py:241, app/agent/llm.py:303 (6 windows)
- app/brokers/paper.py:22, app/brokers/toss_live.py:31, app/engine/orders.py:14 (5 windows)
- app/brokers/paper.py:21, app/brokers/toss_live.py:30 (1 windows)
- app/brokers/paper.py:184, app/brokers/paper.py:196 (1 windows)
- app/brokers/toss_live.py:130, app/market/toss.py:157 (1 windows)

**Slowest tests**

- 3.38 s tests/test_openrouter.py::test_fetch_errors_keep_previous_list
- 1.19 s tests/test_llm_test_endpoint.py::test_check_only_hits_free_endpoint
- 1.01 s tests/test_google_tool.py::test_tool_call_uses_run_id_limit_and_hides_links_in_chat
- 1.01 s tests/test_llm_responses.py::test_real_responses_error_shape_drops_temperature_and_effort
- 1.01 s tests/test_llm_params.py::test_switches_token_param_and_drops_temperature_then_remembers
- 0.55 s tests/test_engine_agent.py::test_llm_tool_loop_uses_toss_tools_and_places_orders
- 0.55 s tests/test_activity.py::test_session_shows_tool_details_and_progress
- 0.53 s tests/test_engine_agent.py::test_llm_forced_submit_after_ignoring_nudge

## 5. Background load

| job | stacks | trigger | active | work | measured per run (sql / toss) |
|---|---|---|---|---|---|
| dispatcher tick | KR+US | cron every minute at :01 (scheduler.py:291) | 24/7 | Scheduler.due(): kv reads; once per local day refresh_calendar (health + calendar) | 2 / 0.0 |
| intraday | KR+US | interval 1 min (scheduler.py:300) | health probe 24/7; work only open-1m..close+5m (engine.py:247-259) | toss_health -> prices(1 symbol) every tick (60 s cache, engine.py:269-277); settle; check_exits -> prices(positions); snapshot_equity every 10th minute | 15 / 2.0 |
| refresh (KR) | KR | interval 10 min (scheduler.py:308-316) | 24/7 - no session gate | sync_data: per symbol in positions + open orders + watchlist: ensure_history + candles(10 d); settle; snapshot_equity (1 row per run) | 49 / 23.0 |
| refresh (US) | US | inside dispatcher tick, minute % 10 == 0 (scheduler.py:248) | open-60m..close+30m only (_near_session) | same as KR refresh | 53 / 24.0 |
| decision session | KR 3/day, US 3/day (default schedule) | dispatcher slots (morning/hourly) | trading days | sync_data + check_exits + agent session (~25-27 tool calls) | - |
| daily report | KR 15:45 / US close+15m | dispatcher slot | trading days | build_daily_report | - |
| UI: market indicators | global | browser poll 60 s (hooks.ts:32) | while a tab is open | server cache 60 s (indicators.py); 8 Toss calls per refresh | 0 / 8.0 |
| UI: portfolio / overview | per viewed market | poll 10 s portfolio (hooks.ts:23), 30 s overview (Overview.tsx:33) | while a tab is open | quotes via prices(positions), 5 s quote cache | - |
| UI: system / agent structure | KR+US | poll 5 s current market + 30 s other (Layout.tsx:77-78); agent/structure 5 s (Agent.tsx:33) | while a tab is open | DB only (kv reads) | - |

**Estimated Toss API calls** (inputs from the live DB: {"kr_positions": 5, "kr_watchlist": 0, "us_positions": 0, "us_watchlist": 9, "toss_calls_per_session": 62})

- idle (both markets closed, no browser open): ~2.6/min
- KR session: ~4.08/min; US session: ~3.98/min
- each open browser tab adds up to ~8/min (indicators, shared cache) + ~6/min per viewed market (portfolio poll)
- per trading day without UI: ~4,948 calls; equity rows/day: KR ~186, US ~90
- assumption: toss_health cache is 60 s and intraday runs every 60 s, so every tick re-probes (1 prices call)
- assumption: quote cache is 5 s, so each 1-min tick refetches quotes when positions exist
- assumption: KR refresh job has no market-hours gate; US refresh runs only near the session
- assumption: session cost uses the in-process scripted 25-tool session

## 6. Observations and hotspots

<!-- MANUAL:BEGIN -->
How to read the numbers. The live DB holds only ~2.5 days of history (50 sessions, 19 orders), so live
latency is mostly fixed cost. A request with no DB work takes ~4.7 ms (`agent_progress`), and each extra DB
session adds ~1 ms on Postgres (`system`: 6 sessions, 13.7 ms; `orders`: 21 sessions, 24 ms). The in-process
bench reproduces a few months of data (2,000 chat rows, 1,000 orders and so on), and its SQL counts show how
latency will grow. Each "Watch" key is a metric that `perf/compare.py` reports.

### Top 10 hotspots (not fixed, measured only)

1. **N+1 symbol-name lookups.** `PriceStore.name_of()` runs `last_bar()`, and `last_bar()` opens a session and runs one `SELECT … ORDER BY day DESC LIMIT 1` per call (app/market/store.py:115-133). It is called once per row in `/api/orders` (app/api/routes.py:255), `/api/fills` (routes.py:278) and `/api/watchlist` (routes.py:1007), and once per position in `PaperBroker.get_positions` (app/brokers/paper.py:182). `get_positions` runs twice per `/api/portfolio`, because `get_balance()` calls it again (app/engine/engine.py:156-157).
   *Evidence:* in-process `/orders` sends 302 statements for 300 rows (41.8 ms) and `/fills` sends 202 (27 ms). `/portfolio` sends 21, 16 of them the same statement. `/overview` sends 52, 32 of them the same statement. Live `/orders` already takes 24 ms for 19 rows.
   *Fix:* keep an in-memory symbol-to-name map in `PriceStore` and refresh it on `upsert`, or do one `IN (…)` lookup per request. Compute positions once per request.
   *Expected:* `/orders` 302 → 2 statements (live at 300 rows: about 300 ms → about 10 ms), `/portfolio` 21 → about 5, `/overview` 52 → about 12.
   *Watch:* `inproc.orders.sql_statements`, `inproc.overview.sql_statements`, `live.orders.p50_ms`

2. **`trade_ledger` replays the whole fill history on every call** (app/engine/trades.py:38-41) and looks up names one fill at a time (trades.py:51). It is called by the `get_trade_history` tool in every session (app/agent/tools.py:470-473) and by every chat reply (app/agent/runner.py:752). In LIVE mode `TossLiveBroker.get_balance` also calls it (app/brokers/toss_live.py:263), so it runs on every portfolio or overview poll, every intraday snapshot and every risk check.
   *Evidence:* one scripted 25-tool session sends 1,163 SQL statements, and 1,047 of them are `daily_prices` name lookups (1,000 fills).
   *Fix:* the name cache from item 1, plus realized PnL kept incrementally (per-symbol average cost updated on each fill) instead of a full replay. Add a `LIMIT` to the recent-fills query.
   *Expected:* about 1,160 → about 120 statements per session, and live-mode portfolio cost no longer grows with history.
   *Watch:* `inproc.agent.llm_scripted.sql_statements`

3. **Settings (kv) are read from the DB on every access, one session per key** (app/engine/settings_store.py:15-18). This covers `engine.status` (engine.py:72-74), `scheduler.config()` and `interval_minutes` (app/engine/scheduler.py:136-143, read 3-4 times in `view()`), `_since()` (routes.py:74-77) and `persona()`/`policy()`.
   *Evidence:* `/agent/structure` runs 16 statements, 10 of them kv reads. `/system` runs 6 (5 kv), `/llm/profile` 5 (5 kv) and `/markets` 4. `/system` and `/agent/structure` are polled every 5 s. Live `app_settings`, a 10-row table, shows about 9k sequential scans. In the 60 s activity sample, with both markets closed and no session running, it was read 73 times/min, out of 110 Postgres commits/min.
   *Fix:* a write-through in-memory cache in `SettingsStore`; one process owns the DB, and `set()` invalidates the cache.
   *Expected:* `/system` 6 → 1 statements (live about 13.7 → about 6 ms), `/agent/structure` 16 → about 6, `/llm/profile` 5 → 0.
   *Watch:* `inproc.system.sql_statements`, `inproc.agent_structure.sql_statements`

4. **List endpoints return large payloads, and nothing is compressed.** `/api/events?limit=200` returns every payload, including `AGENT_PAYLOAD` rows averaging 10.5 KB (max 12 KB): 141 KB live and 377-505 KB in-process. `/api/reports` returns full report payloads of 17-32 KB each, 754 KB for 30 reports in-process. `/api/chat?limit=200` returns 105 KB live; system summary rows reach 32 KB. `/api/agent/decisions?limit=100` returns 87 KB live. There is no `GZipMiddleware` (app/main.py) and no `gzip on` (frontend/nginx.conf); gzip would cut these 4-8x (events 141 → 33 KB, equity 44 → 5.6 KB).
   *Fix:* gzip responses at FastAPI or nginx for anything over about 1 KB. List endpoints should drop or trim `payload`/`evidence` and leave details to a detail call; `/reports` should list metadata only.
   *Expected:* 75-95% fewer bytes on chat, events, reports and decisions.
   *Watch:* `live.events.bytes`, `inproc.reports.bytes`, `*.bytes_gzip`

5. **`/api/llm/costs` is a CPU-bound Python loop.** `costs.summary` loads 12 months of `llm_usage` rows and calls `row_cost()` for every row × every model (app/agent/costs.py:206-244), then repeats that for the last-30-days window (costs.py:263-268).
   *Evidence:* 89 ms in-process with 3,150 rows, with only 3 SQL statements; live is 16 ms (p95 28-51 ms) with 68 rows. Cost grows linearly: about 10 rows per session × 6 sessions/day ≈ 22k rows/year ≈ 0.6 s.
   *Fix:* aggregate in SQL (GROUP BY month, kind, source and model-day), price the aggregates, and cache closed months.
   *Expected:* under 5 ms, flat as history grows.
   *Watch:* `inproc.llm_costs.median_ms`

6. **Background jobs run around the clock even with nothing to do.** The 1-min `intraday` job probes Toss before it checks the session window (engine.py:249 → `toss_health` → `prices`, engine.py:269-277). The health cache lasts 60 s, the same as the interval, so it fires 1 call/min per stack, 24/7. The KR 10-min `refresh` job has no session gate (scheduler.py:308-316; the US job uses `_near_session`). Each run fetches candles for every position and watchlist symbol and writes an equity snapshot (engine.py:244, 337-341).
   *Evidence:* live `equity_snapshots` gets 193-212 rows/day, about 50% written outside 09-15 KST. Estimated idle Toss traffic is about 2.6 calls/min (about 3,700/day). One measured KR refresh costs 49 SQL statements and 23 Toss calls with a 20-symbol watchlist.
   *Fix:* gate KR refresh and the health probe on the session window, and snapshot equity only while the market is open or when equity changed.
   *Expected:* idle Toss calls 2.6/min → about 0; equity rows about 190 → about 45/day.
   *Watch:* `bg.toss_per_min.idle_no_ui`, growth of `db.kr.equity_snapshots.rows`, activity `tup_inserted`

7. **Nothing is ever deleted, and one JSON blob grows without bound.** `AGENT_PAYLOAD` audit rows are 10.5 KB per session: 44 rows hold 460 KB of the 504 KB of audit payloads. Chat `tool` rows are 55% of chat (about 1,000 chat rows/day). `api_cache` holds 198 DART payloads averaging 49 KB (max 230 KB, 9.5 MB raw); it is never pruned and `fetched_at` has no index. `paper_account.state` is one JSON blob holding every order ever placed, rewritten in full on every order or fill (paper.py:120-128): 8 KB live today, 366 KB at 1,000 orders in the bench.
   *Fix:* a retention job that prunes or rolls up chat tool rows and audit payloads older than N days and prunes `api_cache` by `fetched_at`. Store agent payloads outside `audit_events`, or compress them. Keep only open orders in the paper blob; the `orders` table already has the rest.
   *Expected:* DB growth down 70% or more; paper-state writes sized by open orders, not history.
   *Watch:* `inproc.paper_state_bytes_after`, `db.kr.*.total_bytes`

8. **Filter columns lack indexes, and one index is redundant** (KR Postgres and US SQLite). Columns filtered without an index: `agent_runs.started_at` and `status`, `orders.created_at`, `fills.filled_at`, `fills.symbol`, `agent_decisions.created_at`, and `chat_messages.role` (13 columns in total). The memory query `role='user' ORDER BY id DESC LIMIT 5` (runner.py:305-312) runs in every session, every reply and every 5-s `/agent/structure` poll. It walks the chat primary key backwards, costing 1,251 buffer hits for 5 rows today, and it grows by about 1,000 rows/day. `ix_daily_prices_symbol` repeats the leading column of `uq_daily_price(symbol, day)`.
   *Fix:* one alembic migration adding `(started_at)`, `(status, id)`, `orders(created_at)`, `fills(filled_at)`, `fills(symbol)`, `agent_decisions(created_at)` and `chat_messages(role, id)`, and dropping `ix_daily_prices_symbol`.
   *Expected:* small gain today because the tables are small; these queries stay O(log n) as data grows. `memory_user_msgs_5` buffer hits drop from 1,251 to about 5.
   *Watch:* `db.kr.missing_indexes`, `db.kr.explain.memory_user_msgs_5.shared_hit`

9. **Each agent session gets more expensive with every turn.** The full transcript, including raw tool results, is re-sent on every LLM call: 13 KB → 60 KB per call, 236 KB per 25-tool session. `get_supply_demand` and `get_stock_status` each fan out into 5-6 Toss endpoints, for 62 Toss calls per session. Each session also writes 39 chat rows.
   *Fix:* trim older tool results to short summaries and cap the JSON size per tool. Cache Toss responses within a session and batch price calls.
   *Expected:* 30-50% fewer LLM input bytes (lower token cost) and 20-30% fewer Toss calls.
   *Watch:* `inproc.agent.llm_scripted.llm_request_bytes_total`, `inproc.agent.llm_scripted.toss_calls`

10. **The UI polls in a way that repeats server work.** `Layout` polls `/api/system` for both markets (5 s for the current one, 30 s for the other). The Agent page polls `/api/agent/structure` every 5 s; each call runs 16 statements and serializes every tool spec (7-10 KB). The Portfolio page polls every 10 s, even though the WebSocket hub already pushes state and progress (app/engine/events.py:113). `/api/overview` awaits the markets one after another (routes.py:1565) and recomputes each portfolio.
   *Evidence:* one open Agent tab costs about 12×16 + 12×6 + 2×6 ≈ 280 SQL statements/min.
   *Fix:* push engine and agent state over the WebSocket and poll every 30-60 s. Serve `agent/structure` with ETag/304, since it only changes when settings change. Run the per-market work with `asyncio.gather`.
   *Expected:* 70-80% fewer API requests and DB sessions while a tab is open.
   *Watch:* activity `xact_commit`/min with a tab open, `inproc.portfolio.sql_statements`

### Code-quality notes (for the cleanup part of the refactor)

- **Copy-paste candidates.**
  - `build_container` and `_build_us` share 16 duplicated 6-line windows (app/container.py:401/521 and 423/543).
  - The KR and US tool specs are duplicated (app/agent/tools.py:106 vs 289).
  - The Chat Completions and Responses paths in `LLMClient` overlap (app/agent/llm.py:241 vs 303).
  - `_f` (tools.py:31) and `_num` (app/market/toss.py:83) have identical bodies.
- **Complexity.**
  - 15 functions exceed C901 > 10. The largest are `activity.result_line` at 28, `risk.rules.check` at 16 and `run_backtest` at 15.
  - 33 functions are longer than 50 lines; the longest is `build_container` at 152 lines.
  - `routes.py` has 1,647 lines in 14 sections: auth, system, orders, engine, push, agent, LLM, schedule, chat, audit, risk/strategy/watchlist, memo cards, market and markets/overview.
- **Error handling and encapsulation.**
  - There are 40 `except Exception` blocks.
  - `routes.py` reads the private `engine._health` 3 times (lines 205, 206 and 690). The other 4 of the 7 `_health` hits are inside engine.py itself.
- **Healthy baseline.** mypy is clean (67 files), ruff reports 0 violations, and all 304 tests pass in about 23 s.
<!-- MANUAL:END -->
