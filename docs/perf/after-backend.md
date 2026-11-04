# Backend performance baseline (AFTER)

Generated 2026-11-04T14:44:42+00:00 from git `80b1f74` on `main` (109 uncommitted paths in the working tree). Re-run with the commands in `perf/README.md`; compare runs with `python3 perf/compare.py`.

## Headline numbers

- live: slowest p50: **chat 16.44 ms**
- live: largest payload: **events 141,648 B (32,892 B gzipped)**
- live: median of endpoint p50s: **6.68 ms**
- in-process: most SQL per request: **overview 8 statements**
- in-process: total SQL across all endpoints: **47**
- in-process: slowest median: **reports 18.918 ms**
- agent session (25 tool calls): **123 SQL, 62 Toss calls, 235,759 B sent to the LLM**
- live KR DB size: **15.6 MB, 1 unindexed filter columns**
- code: **13,531 lines, C901>10: 9, tests 323 passed in 23.89s**

## 1. Live API latency (docker stack, GET only)

15 sequential requests per endpoint after 2 warm-ups, <= 5 req/s, keep-alive, taken 2026-11-04T14:41:19.004991+00:00 (server state at start: {"KR": {"enabled": true, "open_now": false, "agent_running": false, "engine_status": "RUNNING"}, "US": {"enabled": true, "open_now": true, "agent_running": false, "engine_status": "RUNNING"}}). `bytes_gzip` = size if the response were gzip-compressed (nothing compresses API responses today).

**Calls Toss behind a cache**

| key | path | p50 ms | p95 ms | max ms | bytes | gzip | items |
|---|---|---|---|---|---|---|---|
| overview | /api/overview | 12.6 | 14.6 | 14.7 | 1,289 | 626 | 2 |
| portfolio | /api/portfolio | 9.2 | 12.3 | 15.3 | 1,818 | 857 | 5 |
| llm_costs | /api/llm/costs?months=12 | 10.0 | 12.7 | 13.1 | 11,452 | 1,554 | 12 |
| market_universe | /api/market/universe?limit=40 | 5.0 | 8.1 | 9.3 | 2,373 | 572 | 40 |
| market_indicators | /api/market/indicators | 4.7 | 6.0 | 6.7 | 2,190 | 996 | 6 |
| us_portfolio | /api/us/portfolio | 7.0 | 10.2 | 10.8 | 1,087 | 686 | 9 |
| us_market_universe | /api/us/market/universe?limit=40 | 5.9 | 7.4 | 8.0 | 2,499 | 625 | 40 |

**DB / memory only**

| key | path | p50 ms | p95 ms | max ms | bytes | gzip | items |
|---|---|---|---|---|---|---|---|
| markets | /api/markets | 5.0 | 6.2 | 6.9 | 932 | 365 | 2 |
| system | /api/system | 6.7 | 8.3 | 8.8 | 993 | 660 | 4 |
| agent_structure | /api/agent/structure | 12.2 | 13.1 | 13.7 | 7,320 | 3,413 | 13 |
| chat | /api/chat?limit=200 | 16.4 | 18.5 | 20.0 | 104,448 | 22,865 | 200 |
| events | /api/events?limit=200 | 15.8 | 18.1 | 20.2 | 141,648 | 32,892 | 200 |
| orders | /api/orders?limit=300 | 9.1 | 10.7 | 11.6 | 15,107 | 4,759 | 19 |
| fills | /api/fills?limit=200 | 7.3 | 10.0 | 10.5 | 3,414 | 935 | 19 |
| equity | /api/equity?limit=1000 | 16.1 | 33.9 | 68.2 | 44,083 | 5,653 | 520 |
| agent_runs | /api/agent/runs?limit=20 | 8.0 | 11.4 | 11.7 | 24,898 | 7,660 | 20 |
| agent_decisions | /api/agent/decisions?limit=100 | 13.1 | 16.9 | 20.6 | 86,697 | 21,613 | 100 |
| agent_progress | /api/agent/progress | 4.2 | 5.1 | 5.8 | 47 | 60 | - |
| llm_profile | /api/llm/profile | 4.8 | 8.0 | 8.4 | 3,103 | 899 | - |
| watchlist | /api/watchlist | 6.2 | 9.1 | 9.4 | 2 | 22 | 0 |
| reports | /api/reports | 9.8 | 11.2 | 12.2 | 32,876 | 8,635 | 3 |
| schedule | /api/schedule | 4.8 | 6.6 | 7.6 | 784 | 358 | 9 |
| memory_cards | /api/memory/cards | 6.3 | 7.6 | 8.9 | 93 | 95 | 0 |
| us_system | /api/us/system | 5.6 | 7.8 | 9.7 | 1,104 | 748 | 4 |
| us_agent_structure | /api/us/agent/structure | 8.6 | 9.9 | 10.1 | 5,030 | 2,450 | 10 |
| us_chat | /api/us/chat?limit=200 | 6.7 | 8.5 | 9.4 | 1,899 | 685 | 7 |
| us_events | /api/us/events?limit=200 | 7.2 | 9.1 | 9.4 | 1,711 | 530 | 6 |
| us_orders | /api/us/orders?limit=300 | 6.1 | 8.7 | 10.0 | 2 | 22 | 0 |
| us_fills | /api/us/fills?limit=200 | 5.9 | 8.0 | 9.4 | 2 | 22 | 0 |
| us_equity | /api/us/equity?limit=1000 | 7.4 | 9.2 | 9.4 | 1,185 | 222 | 16 |
| us_agent_runs | /api/us/agent/runs?limit=20 | 6.0 | 7.2 | 7.8 | 1,082 | 319 | 3 |
| us_agent_decisions | /api/us/agent/decisions?limit=100 | 6.1 | 7.2 | 7.3 | 2 | 22 | 0 |
| us_agent_progress | /api/us/agent/progress | 4.6 | 6.0 | 6.2 | 47 | 60 | - |
| us_llm_profile | /api/us/llm/profile | 5.8 | 7.0 | 7.6 | 3,117 | 916 | - |
| us_watchlist | /api/us/watchlist | 8.3 | 11.2 | 11.9 | 807 | 335 | 9 |
| us_reports | /api/us/reports | 6.1 | 8.8 | 9.8 | 2 | 22 | 0 |
| us_schedule | /api/us/schedule | 4.9 | 7.2 | 8.0 | 796 | 382 | 9 |
| us_memory_cards | /api/us/memory/cards | 5.2 | 7.8 | 8.3 | 93 | 95 | 0 |

## 2. In-process deterministic metrics (seeded SQLite, fake Toss)

Seeded rows per stack: daily_prices=10,000, watchlist=20, agent_runs=300, orders=1,000, fills=1,000, agent_decisions=2,100, chat_messages=2,000, audit_events=5,000, equity_snapshots=2,000, llm_usage=3,150, daily_reports=60, memory_cards=20, api_cache=200, paper_state_bytes=73,449. Clock 2026-11-02T10:00:00+09:00. Median of 20 requests. `sql` = statements per request (KR+US engines), `toss` = Toss calls per request at steady state (cold = first call), `dup` = most repeated statement in one request.

| key | median ms | p95 ms | sql | db ms | toss (steady/cold) | bytes | gzip | rows | dup |
|---|---|---|---|---|---|---|---|---|---|
| overview | 2.39 | 2.46 | 8 | 0.35 | 0.0 / 1 | 2,699 | 1,368 | 2 |  |
| markets | 0.60 | 0.64 | 0 | 0.00 | 0.0 / 0 | 730 | 335 | 2 |  |
| system | 0.73 | 0.76 | 1 | 0.01 | 0.0 / 0 | 964 | 608 | 4 |  |
| portfolio | 1.06 | 1.11 | 3 | 0.02 | 0.0 / 0 | 3,831 | 1,136 | 20 |  |
| agent_structure | 2.03 | 2.07 | 4 | 0.06 | 0.0 / 0 | 10,619 | 4,796 | 13 |  |
| chat | 6.00 | 6.09 | 1 | 0.02 | 0.0 / 0 | 164,244 | 42,161 | 200 |  |
| events | 11.13 | 13.15 | 1 | 0.02 | 0.0 / 0 | 377,129 | 97,788 | 200 |  |
| orders | 6.98 | 7.88 | 1 | 0.03 | 0.0 / 0 | 166,104 | 28,685 | 300 |  |
| fills | 2.79 | 3.90 | 1 | 0.02 | 0.0 / 0 | 36,245 | 5,891 | 200 |  |
| equity | 6.92 | 30.37 | 1 | 0.02 | 0.0 / 0 | 79,452 | 16,050 | 1,000 |  |
| agent_runs | 1.57 | 1.74 | 1 | 0.02 | 0.0 / 0 | 25,351 | 8,914 | 20 |  |
| agent_decisions | 4.43 | 5.50 | 1 | 0.03 | 0.0 / 0 | 119,396 | 31,732 | 100 |  |
| agent_progress | 0.57 | 0.66 | 0 | 0.00 | 0.0 / 0 | 47 | 60 | - |  |
| llm_profile | 0.71 | 0.78 | 0 | 0.00 | 0.0 / 0 | 3,710 | 905 | - |  |
| llm_costs | 1.33 | 2.49 | 1 | 0.09 | 0.0 / 8 | 11,712 | 1,864 | 12 |  |
| watchlist | 1.89 | 2.22 | 2 | 0.05 | 0.0 / 0 | 1,852 | 366 | 20 |  |
| market_universe | 0.50 | 0.53 | 0 | 0.00 | 0.0 / 2 | 2,423 | 365 | 40 |  |
| market_indicators | 0.53 | 0.56 | 0 | 0.00 | 0.0 / 0 | 2,539 | 889 | 6 |  |
| reports | 18.92 | 19.77 | 1 | 0.03 | 0.0 / 0 | 753,695 | 192,740 | 30 |  |
| schedule | 0.63 | 0.67 | 0 | 0.00 | 0.0 / 0 | 844 | 360 | 9 |  |
| memory_cards | 1.02 | 1.13 | 1 | 0.01 | 0.0 / 0 | 7,057 | 2,212 | 20 |  |
| us_system | 0.81 | 0.84 | 1 | 0.01 | 0.0 / 0 | 1,066 | 678 | 4 |  |
| us_portfolio | 1.14 | 1.25 | 3 | 0.02 | 0.0 / 0 | 3,994 | 1,298 | 20 |  |
| us_agent_structure | 2.05 | 2.11 | 4 | 0.07 | 0.0 / 0 | 9,854 | 4,746 | 10 |  |
| us_chat | 5.44 | 5.55 | 1 | 0.02 | 0.0 / 0 | 141,129 | 37,281 | 200 |  |
| us_events | 14.14 | 14.39 | 1 | 0.02 | 0.0 / 0 | 504,857 | 129,676 | 200 |  |
| us_orders | 6.69 | 7.95 | 1 | 0.03 | 0.0 / 0 | 165,879 | 29,098 | 300 |  |
| us_fills | 2.73 | 2.82 | 1 | 0.02 | 0.0 / 0 | 36,018 | 5,877 | 200 |  |
| us_equity | 6.75 | 23.66 | 1 | 0.02 | 0.0 / 0 | 76,426 | 16,173 | 1,000 |  |
| us_agent_runs | 1.49 | 1.57 | 1 | 0.02 | 0.0 / 0 | 25,360 | 8,058 | 20 |  |
| us_agent_decisions | 4.36 | 4.48 | 1 | 0.03 | 0.0 / 0 | 119,044 | 30,976 | 100 |  |
| us_agent_progress | 0.55 | 0.59 | 0 | 0.00 | 0.0 / 0 | 47 | 60 | - |  |
| us_llm_profile | 0.72 | 0.79 | 0 | 0.00 | 0.0 / 0 | 3,724 | 915 | - |  |
| us_watchlist | 1.85 | 1.94 | 2 | 0.05 | 0.0 / 0 | 1,784 | 497 | 20 |  |
| us_market_universe | 0.51 | 0.55 | 0 | 0.00 | 0.0 / 3 | 2,424 | 437 | 40 |  |
| us_reports | 18.54 | 18.88 | 1 | 0.02 | 0.0 / 0 | 752,743 | 189,268 | 30 |  |
| us_schedule | 0.65 | 0.72 | 0 | 0.00 | 0.0 / 0 | 858 | 396 | 9 |  |
| us_memory_cards | 1.03 | 1.08 | 1 | 0.01 | 0.0 / 0 | 6,934 | 2,348 | 20 |  |

**Background jobs, cost per invocation** (caches reset to emulate a real 1-min / 10-min tick)

| job | median ms | sql | toss calls | toss by api |
|---|---|---|---|---|
| kr_intraday_1min | 3.15 | 6 | 2.0 | {"prices": 2} |
| kr_sync_data_10min | 24.05 | 48 | 23.0 | {"prices": 1, "candles": 22} |
| kr_scheduler_due_1min | 0.11 | 0 | 0.0 | {} |
| indicators_refresh_60s | 1.21 | 0 | 8.0 | {"index_prices": 1, "prices": 1, "fx": 1, "index_candles": 3, "candles": 2} |
| us_intraday_1min | 5.18 | 11 | 2.0 | {"prices": 2} |
| us_sync_data_10min | 40.00 | 50 | 24.0 | {"prices": 1, "candles": 23} |
| us_scheduler_due_1min | 0.12 | 0 | 0.0 | {} |

**Agent session** (scripted fake LLM via respx, KR stack with seeded history)

| metric | LLM session (scripted) | rule-based session |
|---|---|---|
| LLM calls / tool calls | 6 / 25 | - |
| SQL statements | 123 | 75 |
| Toss calls | 62 | 13 |
| wall ms (LLM client is rate-limited to 2 req/s) | 2,036.6 | 69.3 |
| LLM request bytes per call | 13,463, 26,101, 33,336, 44,810, 58,301, 59,748 | - |
| LLM request bytes total | 235,759 | - |
| rows written chat / audit | 39 / 8 | - |

Most repeated statements in the LLM session:

- 39x `[kr] INSERT INTO chat_messages (ts, run_id, role, kind, content, payload) VALUES (?, ?, ?, ?, ?, ?)…`
- 22x `[kr] SELECT daily_prices.id, daily_prices.symbol, daily_prices.day, daily_prices.name, daily_prices.market, da…`
- 8x `[kr] INSERT INTO audit_events (ts, type, symbol, message, payload) VALUES (?, ?, ?, ?, ?)…`
- 6x `[kr] INSERT INTO llm_usage (ts, kind, run_id, model, calls, input_tokens, cached_tokens, output_tokens, max_in…`
- 6x `[kr] SELECT count(*) AS count_1 FROM daily_prices WHERE daily_prices.symbol = ? AND daily_prices.day >= ? AND …`
- 6x `[kr] SELECT daily_prices.id, daily_prices.symbol, daily_prices.day, daily_prices.name, daily_prices.market, da…`

Paper account JSON blob after seeding 1,000 orders: 73,039 bytes (rewritten in full on every order/fill).

## 3. Live database (read-only)

### KR - PostgreSQL 16.15, database 15,231.0 KB

| table | rows | total | heap | indexes | toast | dead tup | seq scans | idx scans |
|---|---|---|---|---|---|---|---|---|
| chat_messages | 2,172 | 1,680.0 KB | 1,312.0 KB | 272.0 KB | 64.0 KB | 0 | 18 | 606 |
| api_cache | 114 | 1,256.0 KB | 48.0 KB | 48.0 KB | 1,128.0 KB | 0 | 226 | 16 |
| daily_prices | 4,574 | 1,136.0 KB | 624.0 KB | 480.0 KB | 0.0 KB | 62 | 10 | 18,514 |
| corp_codes | 4,009 | 768.0 KB | 512.0 KB | 224.0 KB | 0.0 KB | 0 | 51 | 47 |
| agent_decisions | 336 | 632.0 KB | 512.0 KB | 80.0 KB | 8.0 KB | 7 | 12 | 360 |
| audit_events | 537 | 472.0 KB | 128.0 KB | 96.0 KB | 216.0 KB | 0 | 259 | 34 |
| agent_runs | 51 | 184.0 KB | 104.0 KB | 48.0 KB | 8.0 KB | 5 | 121 | 593 |
| equity_snapshots | 520 | 168.0 KB | 80.0 KB | 64.0 KB | 0.0 KB | 43 | 15 | 316 |
| orders | 19 | 160.0 KB | 16.0 KB | 112.0 KB | 8.0 KB | 5 | 640 | 19 |
| paper_account | 1 | 128.0 KB | 24.0 KB | 16.0 KB | 64.0 KB | 5 | 10 | 27 |
| daily_reports | 3 | 120.0 KB | 16.0 KB | 16.0 KB | 64.0 KB | 0 | 7 | 52 |
| llm_usage | 68 | 88.0 KB | 16.0 KB | 48.0 KB | 0.0 KB | 0 | 91 | 47 |
| fills | 19 | 72.0 KB | 8.0 KB | 64.0 KB | 0.0 KB | 0 | 94 | 1 |
| app_settings | 10 | 64.0 KB | 8.0 KB | 16.0 KB | 8.0 KB | 2 | 17,743 | 0 |
| strategies | 2 | 48.0 KB | 8.0 KB | 32.0 KB | 8.0 KB | 0 | 109 | 0 |
| alembic_version | 1 | 24.0 KB | 8.0 KB | 16.0 KB | 0.0 KB | 3 | 21 | 3 |
| memory_cards | 0 | 24.0 KB | 0.0 KB | 16.0 KB | 8.0 KB | 0 | 130 | 0 |
| users | 0 | 16.0 KB | 0.0 KB | 16.0 KB | 0.0 KB | 0 | 306 | 0 |
| watchlist | 0 | 8.0 KB | 0.0 KB | 8.0 KB | 0.0 KB | 0 | 669 | 0 |

**Largest JSON/TEXT columns** (length of the JSON text)

| column | rows | avg | p95 | max | sum |
|---|---|---|---|---|---|
| api_cache.payload | 114 | 85,137 | 211,086 | 229,895 | 9,478.2 KB |
| chat_messages.payload | 2,172 | 427 | 1,511 | 32,177 | 906.3 KB |
| audit_events.payload | 537 | 962 | 10,794 | 12,215 | 504.4 KB |
| agent_decisions.evidence | 336 | 414 | 785 | 1,285 | 135.7 KB |
| chat_messages.content | 2,172 | 42 | 151 | 627 | 89.4 KB |
| agent_decisions.rationale | 336 | 190 | 288 | 451 | 62.4 KB |
| daily_reports.payload | 3 | 17,008 | 32,165 | 32,165 | 49.8 KB |
| agent_runs.summary | 51 | 341 | 569 | 612 | 17.0 KB |
| audit_events.message | 537 | 20 | 73 | 133 | 10.6 KB |
| agent_runs.market_view | 51 | 185 | 295 | 305 | 9.2 KB |

**audit_events by type**

| type | rows | avg payload | max payload |
|---|---|---|---|
| SIGNAL | 336 | 34 | 34 |
| AGENT_RUN | 49 | 117 | 169 |
| AGENT_PAYLOAD | 44 | 10,495 | 12,215 |
| SYSTEM | 27 | 2 | 2 |
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

**Write rate** - equity snapshots per KST day (and how many outside 09-15h): 2026-11-02: 110 (65), 2026-11-03: 212 (103), 2026-11-04: 198 (113); chat messages per day: 2026-11-02: 812, 2026-11-03: 1093, 2026-11-04: 267.

paper_account.state: 8,126 bytes for 19 orders / 5 positions. api_cache: dart 114 rows avg 85,137 chars max 229,895.

**Live activity sample** (60.3 s, server state {"KR": {"open_now": false, "agent_running": false}, "US": {"open_now": true, "agent_running": false}}; includes scheduler jobs and any browser tab that was open): 37.8 commits/min, 2762.5 tuples returned/min, 0.0 inserted/min; per table: daily_prices seq 0.0/idx 12.0/ins 0.0.

**Indexes**

| table | index | size | scans | definition |
|---|---|---|---|---|
| agent_decisions | agent_decisions_pkey | 16.0 KB | 112 | `btree (id)` |
| agent_decisions | ix_agent_decisions_created_at | 16.0 KB | 17 | `btree (created_at)` |
| agent_decisions | ix_agent_decisions_order_id | 16.0 KB | 0 | `btree (order_id)` |
| agent_decisions | ix_agent_decisions_run_id | 16.0 KB | 213 | `btree (run_id)` |
| agent_decisions | ix_agent_decisions_symbol | 16.0 KB | 18 | `btree (symbol)` |
| agent_runs | agent_runs_pkey | 16.0 KB | 516 | `btree (id)` |
| agent_runs | ix_agent_runs_started_at | 16.0 KB | 58 | `btree (started_at)` |
| agent_runs | ix_agent_runs_status_id | 16.0 KB | 19 | `btree (status, id)` |
| alembic_version | alembic_version_pkc | 16.0 KB | 3 | `btree (version_num)` |
| api_cache | api_cache_pkey | 32.0 KB | 15 | `btree (key)` |
| api_cache | ix_api_cache_fetched_at | 16.0 KB | 1 | `btree (fetched_at)` |
| app_settings | app_settings_pkey | 16.0 KB | 0 | `btree (key)` |
| audit_events | audit_events_pkey | 32.0 KB | 25 | `btree (id)` |
| audit_events | ix_audit_events_symbol | 16.0 KB | 0 | `btree (symbol)` |
| audit_events | ix_audit_events_ts | 32.0 KB | 7 | `btree (ts)` |
| audit_events | ix_audit_events_type | 16.0 KB | 2 | `btree (type)` |
| chat_messages | chat_messages_pkey | 64.0 KB | 548 | `btree (id)` |
| chat_messages | ix_chat_messages_role_id | 88.0 KB | 19 | `btree (role, id)` |
| chat_messages | ix_chat_messages_run_id | 32.0 KB | 0 | `btree (run_id)` |
| chat_messages | ix_chat_messages_ts | 64.0 KB | 39 | `btree (ts)` |
| corp_codes | corp_codes_pkey | 224.0 KB | 47 | `btree (stock_code)` |
| daily_prices | daily_prices_pkey | 120.0 KB | 411 | `btree (id)` |
| daily_prices | ix_daily_prices_day | 56.0 KB | 3,634 | `btree (day)` |
| daily_prices | uq_daily_price | 232.0 KB | 14,470 | `btree (symbol, day)` |
| daily_reports | daily_reports_pkey | 16.0 KB | 52 | `btree (day)` |
| equity_snapshots | equity_snapshots_pkey | 32.0 KB | 151 | `btree (id)` |
| equity_snapshots | ix_equity_snapshots_ts | 32.0 KB | 165 | `btree (ts)` |
| fills | fills_pkey | 16.0 KB | 1 | `btree (id)` |
| fills | ix_fills_filled_at | 16.0 KB | 0 | `btree (filled_at)` |
| fills | ix_fills_order_id | 16.0 KB | 0 | `btree (order_id)` |
| fills | ix_fills_symbol | 16.0 KB | 0 | `btree (symbol)` |
| llm_usage | ix_llm_usage_run_id | 16.0 KB | 0 | `btree (run_id)` |
| llm_usage | ix_llm_usage_ts | 16.0 KB | 47 | `btree (ts)` |
| llm_usage | llm_usage_pkey | 16.0 KB | 0 | `btree (id)` |
| memory_cards | ix_memory_cards_symbol_kind | 8.0 KB | 0 | `btree (symbol, kind)` |
| memory_cards | memory_cards_pkey | 8.0 KB | 0 | `btree (id)` |
| orders | ix_orders_broker_order_id | 16.0 KB | 0 | `btree (broker_order_id)` |
| orders | ix_orders_client_order_id | 16.0 KB | 0 | `btree (client_order_id)` |
| orders | ix_orders_created_at | 16.0 KB | 19 | `btree (created_at)` |
| orders | ix_orders_run_id | 16.0 KB | 0 | `btree (run_id)` |
| orders | ix_orders_status | 16.0 KB | 0 | `btree (status)` |
| orders | ix_orders_symbol | 16.0 KB | 0 | `btree (symbol)` |
| orders | orders_pkey | 16.0 KB | 0 | `btree (id)` |
| paper_account | paper_account_pkey | 16.0 KB | 27 | `btree (id)` |
| strategies | strategies_name_key | 16.0 KB | 0 | `btree (name)` |
| strategies | strategies_pkey | 16.0 KB | 0 | `btree (id)` |
| users | users_pkey | 8.0 KB | 0 | `btree (id)` |
| users | users_username_key | 8.0 KB | 0 | `btree (username)` |
| watchlist | watchlist_pkey | 8.0 KB | 0 | `btree (symbol)` |

Redundant: none

**Filtered/ordered columns without an index** (cross-checked against app code)

| table.column | rows now | used in |
|---|---|---|
| memory_cards.kind | 0 | routes.py:1120 duplicate check |

**EXPLAIN ANALYZE of hot read queries**

| query | exec ms | rows | buffers hit | plan |
|---|---|---|---|---|
| chat_latest_200 | 0.182 | 200 | 139 | Limit > Index Scan on chat_messages using chat_messages_pkey |
| events_latest_200 | 0.230 | 200 | 19 | Limit > Sort > Seq Scan on audit_events |
| orders_since_300 | 0.087 | 19 | 5 | Limit > Sort > Seq Scan on orders |
| fills_since_200 | 0.056 | 19 | 4 | Limit > Sort > Seq Scan on fills |
| equity_since_1000 | 0.197 | 520 | 51 | Limit > Index Scan on equity_snapshots using equity_snapshots_pkey |
| runs_since_20 | 0.151 | 20 | 16 | Limit > Sort > Seq Scan on agent_runs |
| memory_runs_done_3 | 0.029 | 3 | 3 | Limit > Index Scan on agent_runs using ix_agent_runs_status_id |
| memory_user_msgs_5 | 0.049 | 5 | 7 | Limit > Index Scan on chat_messages using ix_chat_messages_role_id |
| last_order_times | 0.028 | 19 | 2 | Seq Scan on orders |
| trade_ledger_all | 0.132 | 19 | 9 | Sort > Hash Join > Seq Scan on fills > Hash > Seq Scan on orders |
| name_of_last_bar | 0.051 | 1 | 4 | Limit > Limit > Seq Scan on daily_prices > Index Scan on daily_prices using uq_daily_price |
| fills_by_symbol | 0.027 | 1 | 2 | Seq Scan on fills > Limit > Seq Scan on fills |
| llm_usage_12mo | 0.039 | 68 | 2 | Seq Scan on llm_usage |

### US - SQLite (wal), files: us_market.db-wal 52.3 KB, us_market.db 444.0 KB, us_market.db-shm 32.0 KB

| table | rows | table bytes | index bytes |
|---|---|---|---|
| agent_runs | 3 | 4,096 | 8,192 |
| alembic_version | 1 | 4,096 | 4,096 |
| app_settings | 6 | 4,096 | 4,096 |
| audit_events | 6 | 4,096 | 12,288 |
| chat_messages | 7 | 4,096 | 12,288 |
| daily_prices | 1,251 | 139,264 | 65,536 |
| equity_snapshots | 16 | 4,096 | 4,096 |
| paper_account | 1 | 4,096 | 0 |
| strategies | 2 | 4,096 | 4,096 |
| watchlist | 9 | 4,096 | 4,096 |

Same schema as KR, so the same columns lack indexes (1 of the checked filter columns).

## 4. Code metrics (backend/app)

- 83 modules, 13,531 lines (11,546 non-blank/non-comment); tests: 34 files, 6,829 lines
- functions: 721, >50 lines: 30, >100 lines: 4
- ruff C901 (max-complexity 10): **9** functions; ruff default rules: 0 violations
- `# type: ignore`: 2, `except Exception`: 42, `# noqa`: 0, TODO/FIXME: 1, private `engine._health` access: 5
- mypy: Success: no issues found in 83 source files (7.4 s)
- pytest: 323 passed in 23.89s (wall 24.5 s, `-p no:warnings -o addopts=`)

**Largest modules**

| module | lines | code |
|---|---|---|
| app/agent/tools.py | 1024 | 955 |
| app/agent/runner.py | 899 | 822 |
| app/market/toss.py | 575 | 509 |
| app/container.py | 568 | 512 |
| app/brokers/paper.py | 535 | 481 |
| app/engine/scheduler.py | 467 | 391 |
| app/engine/engine.py | 414 | 364 |
| app/brokers/toss_live.py | 403 | 351 |
| app/engine/orders.py | 400 | 369 |
| app/agent/costs.py | 396 | 345 |
| app/agent/llm.py | 375 | 326 |
| app/db/models.py | 289 | 229 |
| app/engine/push.py | 285 | 245 |
| app/agent/profiles.py | 276 | 241 |
| app/api/routes/llm.py | 275 | 223 |
| app/api/routes/agent.py | 244 | 210 |
| app/agent/activity.py | 237 | 180 |
| app/research/dart.py | 236 | 207 |
| app/api/routes/memory.py | 233 | 195 |
| app/core/config.py | 233 | 170 |

**C901 complexity > 10**

| function | complexity | location |
|---|---|---|
| check | 16 | app/risk/rules.py:79 |
| run_backtest | 15 | app/backtest/engine.py:58 |
| place_order | 14 | app/brokers/toss_live.py:294 |
| normalize_decisions | 13 | app/agent/runner.py:825 |
| _run_tools | 12 | app/agent/runner.py:369 |
| chat | 11 | app/agent/llm.py:220 |
| _responses | 11 | app/agent/llm.py:277 |
| rule_based_decisions | 11 | app/agent/rule_agent.py:36 |
| _us_market_overview | 11 | app/agent/tools.py:751 |

**Largest functions**

| function | lines | location |
|---|---|---|
| build_container | 154 | app/container.py:293 |
| _build_us | 120 | app/container.py:449 |
| run_backtest | 116 | app/backtest/engine.py:58 |
| _run_tools | 103 | app/agent/runner.py:369 |
| agent_structure | 95 | app/api/routes/agent.py:118 |
| rule_based_decisions | 93 | app/agent/rule_agent.py:36 |
| _us_spec | 93 | app/agent/tools.py:275 |
| run | 87 | app/agent/runner.py:181 |
| build_daily_report | 81 | app/reports/daily.py:15 |
| _execute_one | 80 | app/agent/runner.py:599 |
| submit | 80 | app/engine/orders.py:131 |
| check | 70 | app/agent/llm_check.py:75 |
| t_explore_sector | 69 | app/agent/tools.py:915 |
| _announce | 68 | app/engine/orders.py:251 |
| check | 67 | app/risk/rules.py:79 |
| t_get_price_history | 65 | app/agent/tools.py:527 |
| t_get_financials | 65 | app/agent/tools.py:593 |
| screen | 65 | app/market/toss.py:446 |
| t_get_market_overview | 64 | app/agent/tools.py:686 |
| portfolio | 61 | app/engine/engine.py:159 |

**Duplicate-code hints**: 38 repeated 6-line windows in 8 clusters; identical function bodies: [['app/agent/tools.py:31 _f', 'app/market/toss.py:83 _num']]

- app/container.py:404, app/container.py:526 (9 windows)
- app/agent/tools.py:106, app/agent/tools.py:289 (8 windows)
- app/container.py:426, app/container.py:548 (7 windows)
- app/agent/llm.py:241, app/agent/llm.py:303 (6 windows)
- app/brokers/paper.py:22, app/brokers/toss_live.py:31, app/engine/orders.py:14 (5 windows)
- app/brokers/paper.py:21, app/brokers/toss_live.py:30 (1 windows)
- app/brokers/toss_live.py:130, app/market/toss.py:157 (1 windows)
- app/risk/rules.py:154, app/risk/rules.py:164 (1 windows)

**Slowest tests**

- 3.4 s tests/test_openrouter.py::test_fetch_errors_keep_previous_list
- 1.15 s tests/test_llm_test_endpoint.py::test_check_only_hits_free_endpoint
- 1.02 s tests/test_google_tool.py::test_tool_call_uses_run_id_limit_and_hides_links_in_chat
- 1.01 s tests/test_llm_params.py::test_switches_token_param_and_drops_temperature_then_remembers
- 1.01 s tests/test_llm_responses.py::test_real_responses_error_shape_drops_temperature_and_effort
- 0.55 s tests/test_activity.py::test_session_shows_tool_details_and_progress
- 0.55 s tests/test_engine_agent.py::test_llm_tool_loop_uses_toss_tools_and_places_orders
- 0.53 s tests/test_engine_agent.py::test_llm_forced_submit_after_ignoring_nudge

## 5. Background load

| job | stacks | trigger | active | work | measured per run (sql / toss) |
|---|---|---|---|---|---|
| dispatcher tick | KR+US | cron every minute at :01 (scheduler.py:291) | 24/7 | Scheduler.due(): kv reads; once per local day refresh_calendar (health + calendar) | 0 / 0.0 |
| intraday | KR+US | interval 1 min (scheduler.py:300) | health probe 24/7; work only open-1m..close+5m (engine.py:247-259) | toss_health -> prices(1 symbol) every tick (60 s cache, engine.py:269-277); settle; check_exits -> prices(positions); snapshot_equity every 10th minute | 6 / 2.0 |
| refresh (KR) | KR | interval 10 min (scheduler.py:308-316) | 24/7 - no session gate | sync_data: per symbol in positions + open orders + watchlist: ensure_history + candles(10 d); settle; snapshot_equity (1 row per run) | 48 / 23.0 |
| refresh (US) | US | inside dispatcher tick, minute % 10 == 0 (scheduler.py:248) | open-60m..close+30m only (_near_session) | same as KR refresh | 50 / 24.0 |
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
_(hand-written section - kept across rebuilds)_
<!-- MANUAL:END -->
