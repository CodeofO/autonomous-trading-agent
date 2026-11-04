#!/usr/bin/env python3
"""Merge the raw measurements into one JSON + a readable Markdown report.

    python3 perf/build_report.py                      # before -> docs/perf/baseline-backend.{json,md}
    python3 perf/build_report.py --label after        # after  -> docs/perf/after-backend.{json,md}

Inputs: docs/perf/raw/<label>/{live_api,inprocess,db,code}.json (any missing file is skipped).
The Markdown keeps everything between <!-- MANUAL:BEGIN --> and <!-- MANUAL:END --> across rebuilds
(hand-written observations live there). Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BEGIN, END = "<!-- MANUAL:BEGIN -->", "<!-- MANUAL:END -->"

# --------------------------------------------------------------------------- background jobs (from code)
# Static description of periodic work; per-run costs are filled from inprocess.background (measured).
JOBS: list[dict[str, Any]] = [
    {"job": "dispatcher tick", "stacks": "KR+US", "trigger": "cron every minute at :01 (scheduler.py:291)",
     "active": "24/7", "calls": "Scheduler.due(): kv reads; once per local day refresh_calendar (health + calendar)",
     "measured": "kr_scheduler_due_1min"},
    {"job": "intraday", "stacks": "KR+US", "trigger": "interval 1 min (scheduler.py:300)",
     "active": "health probe 24/7; work only open-1m..close+5m (engine.py:247-259)",
     "calls": "toss_health -> prices(1 symbol) every tick (60 s cache, engine.py:269-277); settle; check_exits "
              "-> prices(positions); snapshot_equity every 10th minute",
     "measured": "kr_intraday_1min"},
    {"job": "refresh (KR)", "stacks": "KR", "trigger": "interval 10 min (scheduler.py:308-316)",
     "active": "24/7 - no session gate",
     "calls": "sync_data: per symbol in positions + open orders + watchlist: ensure_history + candles(10 d); "
              "settle; snapshot_equity (1 row per run)",
     "measured": "kr_sync_data_10min"},
    {"job": "refresh (US)", "stacks": "US", "trigger": "inside dispatcher tick, minute % 10 == 0 (scheduler.py:248)",
     "active": "open-60m..close+30m only (_near_session)", "calls": "same as KR refresh",
     "measured": "us_sync_data_10min"},
    {"job": "decision session", "stacks": "KR 3/day, US 3/day (default schedule)",
     "trigger": "dispatcher slots (morning/hourly)", "active": "trading days",
     "calls": "sync_data + check_exits + agent session (~25-27 tool calls)", "measured": None},
    {"job": "daily report", "stacks": "KR 15:45 / US close+15m", "trigger": "dispatcher slot", "active": "trading days",
     "calls": "build_daily_report", "measured": None},
    {"job": "UI: market indicators", "stacks": "global", "trigger": "browser poll 60 s (hooks.ts:32)",
     "active": "while a tab is open", "calls": "server cache 60 s (indicators.py); 8 Toss calls per refresh",
     "measured": "indicators_refresh_60s"},
    {"job": "UI: portfolio / overview", "stacks": "per viewed market",
     "trigger": "poll 10 s portfolio (hooks.ts:23), 30 s overview (Overview.tsx:33)",
     "active": "while a tab is open", "calls": "quotes via prices(positions), 5 s quote cache", "measured": None},
    {"job": "UI: system / agent structure", "stacks": "KR+US",
     "trigger": "poll 5 s current market + 30 s other (Layout.tsx:77-78); agent/structure 5 s (Agent.tsx:33)",
     "active": "while a tab is open", "calls": "DB only (kv reads)", "measured": None},
]


def load(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text()) if path.exists() else None


def git_info() -> dict[str, Any]:
    def run(*a: str) -> str:
        r = subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True)
        return r.stdout.strip()

    return {"head": run("rev-parse", "--short", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty_files": len([x for x in run("status", "--porcelain").splitlines() if x.strip()])}


def background_estimate(db: dict[str, Any] | None, inproc: dict[str, Any] | None) -> dict[str, Any]:
    """Toss calls per minute (estimate) from live counts + measured per-run costs."""
    kr = (db or {}).get("kr_postgres", {})
    us = (db or {}).get("us_sqlite", {})
    pa = (kr.get("breakdowns", {}).get("paper_account_state") or [{}])[0]
    kr_pos = int(pa.get("positions") or 0)
    kr_watch = int(kr.get("tables", {}).get("watchlist", {}).get("rows") or 0)
    us_pos = int((us.get("paper_account_state") or {}).get("positions") or 0)
    us_watch = int(us.get("tables", {}).get("watchlist", {}).get("rows") or 0)
    sess_calls = ((inproc or {}).get("agent_session", {}).get("llm_scripted", {}).get("toss_calls")) or 62
    n_kr = kr_pos + kr_watch  # upper bound of |positions ∪ watchlist| (open orders ignored)
    n_us = us_pos + us_watch
    kr_refresh = (n_kr + (1 if kr_pos else 0)) / 10
    us_refresh = (n_us + (1 if us_pos else 0)) / 10
    kr_tick = 1 + (1 if kr_pos else 0)
    us_tick = 1 + (1 if us_pos else 0)
    sessions = 3 * sess_calls / 390  # 3 sessions spread over a ~6.5 h session
    est = {
        "inputs": {"kr_positions": kr_pos, "kr_watchlist": kr_watch, "us_positions": us_pos,
                   "us_watchlist": us_watch, "toss_calls_per_session": sess_calls},
        "per_minute": {
            "idle_no_ui": round(1 + kr_refresh + 1, 2),
            "kr_session_no_ui": round(kr_tick + kr_refresh + 1 + sessions, 2),
            "us_session_no_ui": round(us_tick + us_refresh + 1 + kr_refresh + sessions, 2),
            "ui_open_extra": {"indicators": 8, "portfolio_poll_per_viewed_market": 6},
        },
        "per_trading_day_no_ui": round(
            1440 * 2  # health probes, both stacks, 24/7
            + 396 * (1 if kr_pos else 0) + 395 * (1 if us_pos else 0)  # in-session quote refresh
            + 144 * (n_kr + (1 if kr_pos else 0))  # KR 10-min refresh, 24/7
            + 48 * (n_us + (1 if us_pos else 0))  # US 10-min refresh, ~8 h window
            + 6 * sess_calls + 4  # sessions + calendars
        ),
        "equity_rows_per_day": {"kr_estimate": 144 + 39 + 3, "us_estimate": 39 + 48 + 3},
        "assumptions": [
            "toss_health cache is 60 s and intraday runs every 60 s, so every tick re-probes (1 prices call)",
            "quote cache is 5 s, so each 1-min tick refetches quotes when positions exist",
            "KR refresh job has no market-hours gate; US refresh runs only near the session",
            "session cost uses the in-process scripted 25-tool session",
        ],
    }
    return est


# --------------------------------------------------------------------------- markdown helpers
def fmt(v: Any, nd: int = 1) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def kb(v: Any) -> str:
    return "-" if v is None else f"{v / 1024:,.1f} KB"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def render(rep: dict[str, Any], manual: str) -> str:
    md: list[str] = []
    g = rep["git"]
    md.append(f"# Backend performance baseline ({rep['label'].upper()})\n")
    md.append(f"Generated {rep['generated_at']} from git `{g['head']}` on `{g['branch']}` "
              f"({g['dirty_files']} uncommitted paths in the working tree). Re-run with the commands in "
              "`perf/README.md`; compare runs with `python3 perf/compare.py`.\n")
    s = rep.get("summary", {})
    if s:
        md.append("## Headline numbers\n")
        md += [f"- {k}: **{v}**" for k, v in s.items()]
        md.append("")

    live = rep.get("live_api")
    if live:
        st = live.get("server_state_before", {})
        md.append("## 1. Live API latency (docker stack, GET only)\n")
        md.append(f"{live['params']['n']} sequential requests per endpoint after {live['params']['warmup']} warm-ups, "
                  f"<= {1 / live['params']['min_interval_s']:.0f} req/s, keep-alive, taken {live['started_at']} "
                  f"(server state at start: {json.dumps(st, ensure_ascii=False)}). `bytes_gzip` = size if the "
                  "response were gzip-compressed (nothing compresses API responses today).\n")
        for title, src in (("Calls Toss behind a cache", ("toss_cached", "toss_live")), ("DB / memory only", ("db",))):
            rows = [[k, e["path"], fmt(e["p50_ms"]), fmt(e["p95_ms"]), fmt(e["max_ms"]), fmt(e["bytes"]),
                     fmt(e.get("bytes_gzip")), fmt(e["items"])]
                    for k, e in live["endpoints"].items() if e["source"] in src]
            md.append(f"**{title}**\n")
            md.append(table(["key", "path", "p50 ms", "p95 ms", "max ms", "bytes", "gzip", "items"], rows))
            md.append("")

    ip = rep.get("inprocess")
    if ip:
        md.append("## 2. In-process deterministic metrics (seeded SQLite, fake Toss)\n")
        md.append("Seeded rows per stack: " + ", ".join(f"{k}={fmt(v)}" for k, v in ip["seed_rows"]["KR"].items())
                  + f". Clock {ip['env']['clock']}. Median of {ip['params']['n']} requests. "
                  "`sql` = statements per request (KR+US engines), `toss` = Toss calls per request at steady "
                  "state (cold = first call), `dup` = most repeated statement in one request.\n")
        rows = []
        for k, e in ip["endpoints"].items():
            dup = e.get("repeated_statements") or []
            rows.append([k, fmt(e["median_ms"], 2), fmt(e["p95_ms"], 2), e["sql_statements"], fmt(e["db_ms_median"], 2),
                         f"{fmt(e['toss_calls_mean'], 1)} / {e['cold']['toss_calls']}", fmt(e["bytes"]),
                         fmt(e.get("bytes_gzip")), fmt(e["items"]), f"{dup[0]['count']}x" if dup else ""])
        md.append(table(["key", "median ms", "p95 ms", "sql", "db ms", "toss (steady/cold)", "bytes", "gzip",
                         "rows", "dup"], rows))
        md.append("\n**Background jobs, cost per invocation** (caches reset to emulate a real 1-min / 10-min tick)\n")
        md.append(table(["job", "median ms", "sql", "toss calls", "toss by api"],
                        [[k, fmt(b["median_ms"], 2), b["sql_statements"], fmt(b["toss_calls_mean"], 1),
                          json.dumps(b.get("toss_by_api", {}))] for k, b in ip["background"].items()]))
        a = ip["agent_session"]["llm_scripted"]
        r = ip["agent_session"]["rule_based"]
        md.append("\n**Agent session** (scripted fake LLM via respx, KR stack with seeded history)\n")
        md.append(table(["metric", "LLM session (scripted)", "rule-based session"], [
            ["LLM calls / tool calls", f"{a['llm_calls']} / {a['tool_calls']}", "-"],
            ["SQL statements", a["sql_statements"], r["sql_statements"]],
            ["Toss calls", a["toss_calls"], r["toss_calls"]],
            ["wall ms (LLM client is rate-limited to 2 req/s)", fmt(a["wall_ms"]), fmt(r["wall_ms"])],
            ["LLM request bytes per call", ", ".join(fmt(x) for x in a["llm_request_bytes"]), "-"],
            ["LLM request bytes total", fmt(a["llm_request_bytes_total"]), "-"],
            ["rows written chat / audit", f"{a['chat_rows_written']} / {a['audit_rows_written']}", "-"],
        ]))
        md.append("\nMost repeated statements in the LLM session:\n")
        md += [f"- {x['count']}x `{x['statement'][:110]}…`" for x in a.get("repeated_statements", [])]
        md.append(f"\nPaper account JSON blob after seeding 1,000 orders: {fmt(ip.get('paper_state_bytes_after'))} bytes "
                  "(rewritten in full on every order/fill).\n")

    db = rep.get("db")
    if db and "kr_postgres" in db and "error" not in db["kr_postgres"]:
        k = db["kr_postgres"]
        md.append("## 3. Live database (read-only)\n")
        md.append(f"### KR - PostgreSQL {k['version']}, database {kb(k['database_bytes'])}\n")
        rows = [[t, fmt(v["rows"]), kb(v["total_bytes"]), kb(v["heap_bytes"]), kb(v["index_bytes"]),
                 kb(v["toast_bytes"]), fmt(v["n_dead_tup"]), fmt(v["seq_scan"]), fmt(v["idx_scan"])]
                for t, v in k["tables"].items()]
        md.append(table(["table", "rows", "total", "heap", "indexes", "toast", "dead tup", "seq scans", "idx scans"],
                        rows))
        md.append("\n**Largest JSON/TEXT columns** (length of the JSON text)\n")
        md.append(table(["column", "rows", "avg", "p95", "max", "sum"],
                        [[c, fmt(v["n"]), fmt(v["avg_len"]), fmt(v["p95_len"]), fmt(v["max_len"]), kb(v["sum_len"])]
                         for c, v in list(k["column_sizes"].items())[:10]]))
        b = k["breakdowns"]
        md.append("\n**audit_events by type**\n")
        md.append(table(["type", "rows", "avg payload", "max payload"],
                        [[x["type"], x["n"], fmt(x["avg_payload"]), fmt(x["max_payload"])]
                         for x in b["audit_events_by_type"]]))
        md.append("\n**chat_messages by role/kind**\n")
        md.append(table(["role", "kind", "rows", "avg payload", "max payload", "avg content"],
                        [[x["role"], x["kind"], x["n"], fmt(x["avg_payload"]), fmt(x["max_payload"]),
                          fmt(x["avg_content"])] for x in b["chat_messages_by_role_kind"]]))
        md.append("\n**Write rate** - equity snapshots per KST day (and how many outside 09-15h): "
                  + ", ".join(f"{x['day']}: {x['n']} ({x['outside_kr_session']})"
                              for x in b.get("equity_snapshots_per_kst_day", []))
                  + "; chat messages per day: "
                  + ", ".join(f"{x['day']}: {x['n']}" for x in b.get("chat_messages_per_kst_day", [])) + ".\n")
        pa = (b.get("paper_account_state") or [{}])[0]
        md.append(f"paper_account.state: {fmt(pa.get('bytes'))} bytes for {pa.get('orders')} orders / "
                  f"{pa.get('positions')} positions. api_cache: "
                  + ", ".join(f"{x['prefix']} {x['n']} rows avg {fmt(x['avg_payload'])} chars max {fmt(x['max_payload'])}"
                              for x in b["api_cache_by_prefix"]) + ".\n")
        act = k.get("activity")
        if act:
            pm = act["per_minute"]
            md.append(f"**Live activity sample** ({act['seconds']} s, server state {json.dumps(act['server_state'])}; "
                      "includes scheduler jobs and any browser tab that was open): "
                      f"{pm['xact_commit']} commits/min, {pm['tup_returned']} tuples returned/min, "
                      f"{pm['tup_inserted']} inserted/min; per table: "
                      + ", ".join(f"{t} seq {v['seq_scan']}/idx {v['idx_scan']}/ins {v['n_tup_ins']}"
                                  for t, v in act["tables_per_minute"].items()) + ".\n")
        md.append("**Indexes**\n")
        md.append(table(["table", "index", "size", "scans", "definition"],
                        [[i["table"], i["index"], kb(i["bytes"]), fmt(i["idx_scan"]),
                          f"`{i['indexdef'].split(' USING ')[-1]}`"] for i in k["indexes"]]))
        md.append("\nRedundant: " + (", ".join(f"`{r['index']}` (covered by `{r['covered_by']}`)"
                                               for r in k["redundant_indexes"]) or "none") + "\n")
        md.append("**Filtered/ordered columns without an index** (cross-checked against app code)\n")
        md.append(table(["table.column", "rows now", "used in"],
                        [[f"{m['table']}.{m['column']}", fmt(m["rows"]), m["used_in"]] for m in k["missing_indexes"]]))
        md.append("\n**EXPLAIN ANALYZE of hot read queries**\n")
        md.append(table(["query", "exec ms", "rows", "buffers hit", "plan"],
                        [[q, fmt(p.get("execution_ms"), 3), fmt(p.get("rows")), fmt(p.get("shared_hit")),
                          " > ".join(p.get("nodes", []))] for q, p in k["explain"].items()]))
    if db and "us_sqlite" in db and "error" not in db["us_sqlite"]:
        u = db["us_sqlite"]
        md.append(f"\n### US - SQLite ({u['journal_mode']}), files: "
                  + ", ".join(f"{n} {kb(sz)}" for n, sz in u["file_bytes"].items()) + "\n")
        md.append(table(["table", "rows", "table bytes", "index bytes"],
                        [[t, fmt(v["rows"]), fmt(v["table_bytes"]), fmt(v["index_bytes"])]
                         for t, v in u["tables"].items() if v["rows"]]))
        md.append(f"\nSame schema as KR, so the same columns lack indexes "
                  f"({len(u['missing_indexes'])} of the checked filter columns).\n")

    code = rep.get("code")
    if code:
        md.append("## 4. Code metrics (backend/app)\n")
        c901 = code["complexity_c901"]
        gc = code["grep_counts"]
        md.append(f"- {code['app']['files']} modules, {fmt(code['app']['lines'])} lines "
                  f"({fmt(code['app']['code_lines'])} non-blank/non-comment); tests: {code['tests_src']['files']} files, "
                  f"{fmt(code['tests_src']['lines'])} lines")
        md.append(f"- functions: {code['functions']['count']}, >50 lines: {code['functions']['over_50_lines']}, "
                  f">100 lines: {code['functions']['over_100_lines']}")
        md.append(f"- ruff C901 (max-complexity 10): **{c901['count']}** functions; ruff default rules: "
                  f"{code['ruff_default_rules']['violations']} violations")
        md.append(f"- `# type: ignore`: {gc['type_ignore']}, `except Exception`: {gc['except_exception']}, "
                  f"`# noqa`: {gc['noqa']}, TODO/FIXME: {gc['todo_fixme']}, private `engine._health` access: "
                  f"{gc['private_access_engine_health']}")
        if "mypy" in code:
            md.append(f"- mypy: {code['mypy']['summary']} ({code['mypy']['seconds']} s)")
        if "pytest" in code:
            p = code["pytest"]
            md.append(f"- pytest: {p['summary']} (wall {p['wall_s']} s, `-p no:warnings -o addopts=`)")
        md.append("\n**Largest modules**\n")
        md.append(table(["module", "lines", "code"], [[m["file"], m["lines"], m["code"]] for m in code["app"]["top_modules"]]))
        md.append("\n**C901 complexity > 10**\n")
        md.append(table(["function", "complexity", "location"],
                        [[f["function"], f["complexity"], f"{f['file']}:{f['line']}"] for f in c901["top"]]))
        md.append("\n**Largest functions**\n")
        md.append(table(["function", "lines", "location"],
                        [[f["function"], f["lines"], f"{f['file']}:{f['line']}"] for f in code["functions"]["largest"]]))
        d = code["duplicates"]
        md.append(f"\n**Duplicate-code hints**: {d['windows']['duplicated_windows']} repeated "
                  f"{d['windows']['window_lines']}-line windows in {d['windows']['clusters']} clusters; identical "
                  f"function bodies: {d['identical_function_bodies'] or 'none'}\n")
        md += [f"- {', '.join(cl['locations'])} ({cl['windows']} windows)" for cl in d["windows"]["top"][:8]]
        if "pytest" in code and code["pytest"]["slowest"]:
            md.append("\n**Slowest tests**\n")
            md += [f"- {t['seconds']} s {t['test']}" for t in code["pytest"]["slowest"][:8]]
        md.append("")

    bg = rep.get("background_load")
    if bg:
        md.append("## 5. Background load\n")
        meas = (ip or {}).get("background", {})
        md.append(table(["job", "stacks", "trigger", "active", "work", "measured per run (sql / toss)"],
                        [[j["job"], j["stacks"], j["trigger"], j["active"], j["calls"],
                          (f"{meas[j['measured']]['sql_statements']} / {fmt(meas[j['measured']]['toss_calls_mean'], 1)}"
                           if j["measured"] and j["measured"] in meas else "-")] for j in bg["jobs"]]))
        e = bg["estimate"]
        md.append(f"\n**Estimated Toss API calls** (inputs from the live DB: {json.dumps(e['inputs'])})\n")
        pm = e["per_minute"]
        md.append(f"- idle (both markets closed, no browser open): ~{pm['idle_no_ui']}/min")
        md.append(f"- KR session: ~{pm['kr_session_no_ui']}/min; US session: ~{pm['us_session_no_ui']}/min")
        md.append(f"- each open browser tab adds up to ~{pm['ui_open_extra']['indicators']}/min (indicators, shared "
                  f"cache) + ~{pm['ui_open_extra']['portfolio_poll_per_viewed_market']}/min per viewed market (portfolio poll)")
        md.append(f"- per trading day without UI: ~{fmt(e['per_trading_day_no_ui'])} calls; equity rows/day: "
                  f"KR ~{e['equity_rows_per_day']['kr_estimate']}, US ~{e['equity_rows_per_day']['us_estimate']}")
        md += [f"- assumption: {x}" for x in e["assumptions"]]
        md.append("")

    md.append("## 6. Observations and hotspots\n")
    md.append(BEGIN)
    md.append(manual.strip() if manual.strip() else "_(hand-written section - kept across rebuilds)_")
    md.append(END)
    md.append("")
    return "\n".join(md)


def summary(rep: dict[str, Any]) -> dict[str, Any]:
    s: dict[str, Any] = {}
    live = rep.get("live_api")
    if live:
        eps = live["endpoints"]
        slow = max(eps.items(), key=lambda kv: kv[1]["p50_ms"])
        big = max(eps.items(), key=lambda kv: kv[1]["bytes"])
        s["live: slowest p50"] = f"{slow[0]} {slow[1]['p50_ms']} ms"
        s["live: largest payload"] = f"{big[0]} {big[1]['bytes']:,} B ({big[1].get('bytes_gzip', 0):,} B gzipped)"
        s["live: median of endpoint p50s"] = f"{sorted(e['p50_ms'] for e in eps.values())[len(eps) // 2]} ms"
    ip = rep.get("inprocess")
    if ip:
        eps = ip["endpoints"]
        worst = max(eps.items(), key=lambda kv: kv[1]["sql_statements"])
        s["in-process: most SQL per request"] = f"{worst[0]} {worst[1]['sql_statements']} statements"
        s["in-process: total SQL across all endpoints"] = sum(e["sql_statements"] for e in eps.values())
        s["in-process: slowest median"] = (lambda kv: f"{kv[0]} {kv[1]['median_ms']} ms")(
            max(eps.items(), key=lambda kv: kv[1]["median_ms"]))
        a = ip["agent_session"]["llm_scripted"]
        s["agent session (25 tool calls)"] = (f"{a['sql_statements']} SQL, {a['toss_calls']} Toss calls, "
                                              f"{a['llm_request_bytes_total']:,} B sent to the LLM")
    db = rep.get("db")
    if db and "kr_postgres" in db and "tables" in db["kr_postgres"]:
        k = db["kr_postgres"]
        s["live KR DB size"] = f"{k['database_bytes'] / 1e6:.1f} MB, {len(k['missing_indexes'])} unindexed filter columns"
    code = rep.get("code")
    if code:
        s["code"] = (f"{code['app']['lines']:,} lines, C901>10: {code['complexity_c901']['count']}, "
                     f"tests {code.get('pytest', {}).get('summary', '-')}")
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before")
    a = ap.parse_args()
    raw = ROOT / "docs/perf/raw" / a.label
    name = "baseline-backend" if a.label == "before" else f"{a.label}-backend"
    rep: dict[str, Any] = {
        "schema": "perf-backend/v1",
        "label": a.label,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git": git_info(),
        "live_api": load(raw / "live_api.json"),
        "inprocess": load(raw / "inprocess.json"),
        "db": load(raw / "db.json"),
        "code": load(raw / "code.json"),
    }
    rep["background_load"] = {"jobs": JOBS, "estimate": background_estimate(rep["db"], rep["inprocess"])}
    rep["summary"] = summary(rep)
    out_json = ROOT / "docs/perf" / f"{name}.json"
    out_md = ROOT / "docs/perf" / f"{name}.md"
    out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    manual = ""
    if out_md.exists():
        txt = out_md.read_text()
        if BEGIN in txt and END in txt:
            manual = txt.split(BEGIN, 1)[1].split(END, 1)[0]
    out_md.write_text(render(rep, manual))
    print(f"wrote {out_json}\nwrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
