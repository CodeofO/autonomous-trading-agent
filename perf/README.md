# perf/ - backend BEFORE/AFTER measurement kit

Measurement only. Nothing here changes app code, tests or live data:

- the live API bench sends **GET requests only**, one at a time, at most 5 req/s;
- the DB inspection runs SELECT/EXPLAIN only, in `default_transaction_read_only` psql sessions, and copies the
  US SQLite file out of the container into `perf/tmp/` (git-ignored) before reading it;
- the in-process bench builds the app on a temporary SQLite DB with the test fakes. It never reads `.env`
  and blocks outbound sockets.

Review the scripts before pointing them at anything other than the local docker stack.

## Run order (BEFORE = default label `before`)

From the repo root, with the docker stack up (backend :18000, Postgres service `db`):

```bash
# 1) live API latency, GET only (~2.5 min). --require-idle aborts if an agent session is running.
python3 perf/bench_live_api.py --require-idle            # -> docs/perf/raw/before/live_api.json

# 2) live DB: sizes, indexes, JSON sizes, EXPLAIN, 60 s pg_stat activity sample (~1.5 min)
python3 perf/db_inspect.py                               # -> docs/perf/raw/before/db.json

# 3) in-process deterministic bench (~15 s): SQL statements / Toss calls / payload per endpoint
(cd backend && uv run python ../perf/bench_backend.py)   # -> docs/perf/raw/before/inprocess.json

# 4) code metrics + pytest + mypy (~35 s)
(cd backend && uv run python ../perf/code_metrics.py)    # -> docs/perf/raw/before/code.json

# 5) merge -> docs/perf/baseline-backend.json + .md (keeps the hand-written observations block)
python3 perf/build_report.py
```

## AFTER run and comparison

Same commands with `--label after` (the build step writes `docs/perf/after-backend.{json,md}`):

```bash
python3 perf/bench_live_api.py --require-idle --label after
python3 perf/db_inspect.py --label after
(cd backend && uv run python ../perf/bench_backend.py --label after)
(cd backend && uv run python ../perf/code_metrics.py --label after)
python3 perf/build_report.py --label after
python3 perf/compare.py docs/perf/baseline-backend.json docs/perf/after-backend.json --only-changed \
    --out docs/perf/compare-backend.md
```

`compare.py` flattens ~540 numeric metrics (latency, SQL statements, Toss calls, bytes, table sizes,
code counts) and prints before / after / delta / delta %.

## Files

| file | what |
|---|---|
| `endpoints.py` | shared endpoint list (keys are the comparison keys; query params match the frontend) |
| `bench_live_api.py` | live latency p50/p95/max, bytes, gzip size, rows (stdlib only) |
| `db_inspect.py` | Postgres + US SQLite: row counts, sizes, dead tuples, indexes, redundant/missing indexes, JSON column sizes, EXPLAIN ANALYZE, write-rate breakdowns, 60 s activity sample |
| `bench_backend.py` | in-process app on seeded SQLite: wall time, SQL statements (`before_cursor_execute`), DB ms, Toss calls, bytes, repeated statements; background-job and agent-session costs (scripted fake LLM via respx) |
| `code_metrics.py` | LOC, ruff C901 > 10, largest functions, duplicate hints, `type: ignore` / `except Exception` counts, mypy, pytest |
| `build_report.py` | merges raw JSON into `docs/perf/<label>-backend.json` + `.md` (`baseline-backend.*` for `before`) |
| `compare.py` | BEFORE vs AFTER diff table |

## Keep the comparison fair

- Run the live bench when no agent session is running (`--require-idle`) and note the market state; the
  JSON records `server_state_before/after`.
- Do not change `VOLUME`, the RNG seed, the clock or `endpoints.py` between BEFORE and AFTER. If an
  endpoint is renamed, keep its key and update the path.
- The in-process numbers are deterministic (same seed, same row counts, same bytes); wall times vary by a
  few percent, so compare SQL counts / Toss calls / bytes first.
- The agent-session wall time is dominated by the LLM client's 2 req/s rate limiter, so compare its
  SQL / Toss / bytes numbers, not the wall time.
- Frontend metrics (bundle size, render jank) are not covered here.

---

## Frontend

Separate kit for the React app (`frontend/`), files prefixed `fe_`. Node only (no new dependencies:
Playwright and Vite come from `frontend/node_modules`). Nothing here edits `frontend/` source, tests,
`vite.config.ts` or `frontend/dist`; builds go to `perf/tmp/` (git-ignored).

Safety against the live paper-trading system: the browser scripts only load pages and click the
MarketSwitch (전체/국장/미장, a client-side view switch). Every page gets a guard (`GUARD_SCRIPT` in
`fe_lib.mjs`) that makes `fetch`/`XMLHttpRequest`/`sendBeacon` refuse anything but GET/HEAD/OPTIONS, and a
CDP listener aborts the run if a non-GET `/api` request still reaches the network. (The Settings page fires
`POST /api/llm/openrouter/models/refresh` on its own when the OpenRouter list is stale; the guard blocks it
and the report lists it under "blocked writes".)

### Run order (BEFORE = label `before`)

From the repo root with the docker stack up (frontend nginx on :15173, backend on :18000). Do not run
anything CPU-heavy (vitest, builds, backend benches) while step 2 runs.

```bash
# 1) bundle: vite build into perf/tmp/fe_build with sourcemaps (+ what nginx serves)   (~5 s)
node perf/fe_bundle.mjs --label before                          # -> docs/perf/raw/before/fe_bundle.json

# 2) browser: page loads (13 routes x desktop / mobile 4x CPU / mobile 4x CPU + Fast 3G),
#    60 s idle on overview/dashboard/room, chat scroll + MarketSwitch, React render counts  (~20 min)
node perf/fe_browser.mjs --suite load,steady,interact,render --label before
#    -> fe_pageload.json, fe_steady.json, fe_interaction.json, fe_render.json
#    (the render suite builds an unminified copy into perf/tmp/fe_build_named and serves it with
#     `vite preview` on 127.0.0.1:15183, proxying /api and /ws to --backend-url; it stops it afterwards)

# 3) code metrics: LOC, components, useQuery/refetchInterval, casts, CSS, eslint, prettier, tsc, vitest (~40 s)
node perf/fe_code_metrics.mjs --label before                    # -> docs/perf/raw/before/fe_code.json

# 4) merge -> docs/perf/baseline-frontend.json + .md (keeps the hand-written MANUAL block)
node perf/fe_build_report.mjs --label before
```

Useful flags: `--base-url` (default `http://localhost:15173`), `--runs 3`, `--seconds 60` (steady),
`--render-seconds 60`, `--routes overview,room_kr` and `--profiles desktop` (subset runs; keep the defaults for
the real baseline), `--backend-url http://127.0.0.1:18000`, `--render-port 15183`, `--reuse-build`.

### AFTER run and comparison

```bash
node perf/fe_bundle.mjs --label after
node perf/fe_browser.mjs --suite load,steady,interact,render --label after
node perf/fe_code_metrics.mjs --label after
node perf/fe_build_report.mjs --label after                     # -> docs/perf/after-frontend.{json,md}
node perf/fe_compare.mjs --headline-only                         # ~70 headline numbers
node perf/fe_compare.mjs docs/perf/baseline-frontend.json docs/perf/after-frontend.json \
    --only-changed --min-pct 5 --out docs/perf/compare-frontend.md
```

The live nginx container serves whatever was built into the image: rebuild/restart the frontend container
after the refactor before the AFTER browser run (fe_bundle.json records what the server actually sends).

### Files

| file | what |
|---|---|
| `fe_lib.mjs` | shared: routes + "content visible" predicates (`ROUTES`), device/network profiles, write guard, in-page collectors (LCP, CLS, long tasks, Event Timing, View Transitions, canvas insertions, React DevTools-hook commit/render counter), CDP network/WS/metrics tracker |
| `fe_bundle.mjs` | build into perf/tmp, per-chunk raw/gzip/brotli, bytes per npm package via sourcemaps, code-splitting facts, live Content-Encoding/Cache-Control |
| `fe_browser.mjs` | suites `load`, `steady`, `interact`, `render` (see header comment) |
| `fe_code_metrics.mjs` | static code metrics + eslint/prettier/tsc/vitest |
| `fe_build_report.mjs` | merge into `docs/perf/<baseline|label>-frontend.{json,md}` |
| `fe_compare.mjs` | BEFORE vs AFTER diff of every numeric leaf (or `--headline-only`) |

### Keep the comparison fair

- Same machine, same profiles, nothing else heavy running. Each page load is a fresh browser context (cold
  cache), 3 runs, median; Fast 3G is a single run.
- The data is live: chat/events/orders grow and the agent may be running. The JSON records the market state
  (`open_now`, `agent_running`) for every run; take AFTER at a similar market state (e.g. both with KR and US
  closed and no agent session) and compare request counts / bytes / renders before wall times.
- Route keys and the JSON layout are the comparison keys. If markup changes, update the `ready` predicate
  in `ROUTES` (fe_lib.mjs) so it still means "this page's data is on screen", but keep the key.
- Steady-state WS frames depend on agent activity; compare `api_requests_per_min`, `react_commits_per_min`,
  `canvas_added_per_min`, `script_ms_per_min` and render counts first.
