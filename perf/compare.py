#!/usr/bin/env python3
"""Compare two merged reports (BEFORE vs AFTER) and print a Markdown diff table.

    python3 perf/compare.py                                   # baseline-backend.json vs after-backend.json
    python3 perf/compare.py A.json B.json --only-changed --min-pct 5 --out docs/perf/compare-backend.md

Lower is better for every metric listed here except pytest.total (more tests is fine).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent


def metrics(rep: dict[str, Any]) -> dict[str, float]:
    m: dict[str, float] = {}

    def put(key: str, v: Any) -> None:
        if isinstance(v, int | float) and not isinstance(v, bool):
            m[key] = float(v)

    for k, e in ((rep.get("live_api") or {}).get("endpoints") or {}).items():
        for f in ("p50_ms", "p95_ms", "bytes", "bytes_gzip"):
            put(f"live.{k}.{f}", e.get(f))
    ip = rep.get("inprocess") or {}
    for k, e in (ip.get("endpoints") or {}).items():
        for f in ("median_ms", "p95_ms", "sql_statements", "toss_calls_mean", "bytes", "bytes_gzip"):
            put(f"inproc.{k}.{f}", e.get(f))
        put(f"inproc.{k}.cold_toss", (e.get("cold") or {}).get("toss_calls"))
    for k, e in (ip.get("background") or {}).items():
        for f in ("median_ms", "sql_statements", "toss_calls_mean"):
            put(f"inproc.bg.{k}.{f}", e.get(f))
    for k, e in (ip.get("agent_session") or {}).items():
        for f in ("sql_statements", "toss_calls", "llm_request_bytes_total", "tool_calls", "llm_calls"):
            put(f"inproc.agent.{k}.{f}", e.get(f))
    put("inproc.paper_state_bytes_after", ip.get("paper_state_bytes_after"))
    kr = (rep.get("db") or {}).get("kr_postgres") or {}
    put("db.kr.database_bytes", kr.get("database_bytes"))
    if "missing_indexes" in kr:
        put("db.kr.missing_indexes", len(kr["missing_indexes"]))
        put("db.kr.redundant_indexes", len(kr.get("redundant_indexes", [])))
    for f, v in ((kr.get("activity") or {}).get("per_minute") or {}).items():
        put(f"db.kr.activity_per_min.{f}", v)
    for t, v in (kr.get("tables") or {}).items():
        for f in ("rows", "total_bytes", "n_dead_tup"):
            put(f"db.kr.{t}.{f}", v.get(f))
    for q, p in (kr.get("explain") or {}).items():
        put(f"db.kr.explain.{q}.shared_hit", p.get("shared_hit"))
    code = rep.get("code") or {}
    put("code.app_lines", (code.get("app") or {}).get("lines"))
    put("code.c901_count", (code.get("complexity_c901") or {}).get("count"))
    put("code.functions_over_50", (code.get("functions") or {}).get("over_50_lines"))
    put("code.dup_windows", ((code.get("duplicates") or {}).get("windows") or {}).get("duplicated_windows"))
    for k, v in (code.get("grep_counts") or {}).items():
        put(f"code.{k}", v)
    put("code.mypy_errors", (code.get("mypy") or {}).get("errors"))
    put("code.pytest_total", (code.get("pytest") or {}).get("total"))
    put("code.pytest_wall_s", (code.get("pytest") or {}).get("wall_s"))
    est = ((rep.get("background_load") or {}).get("estimate") or {}).get("per_minute") or {}
    for k in ("idle_no_ui", "kr_session_no_ui", "us_session_no_ui"):
        put(f"bg.toss_per_min.{k}", est.get(k))
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("before", nargs="?", default=str(ROOT / "docs/perf/baseline-backend.json"))
    ap.add_argument("after", nargs="?", default=str(ROOT / "docs/perf/after-backend.json"))
    ap.add_argument("--only-changed", action="store_true")
    ap.add_argument("--min-pct", type=float, default=0.0, help="hide rows whose |change| is below this %")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    b = metrics(json.loads(Path(a.before).read_text()))
    c = metrics(json.loads(Path(a.after).read_text()))
    rows = []
    for k in sorted(set(b) | set(c)):
        x, y = b.get(k), c.get(k)
        if x is not None and y is not None:
            d = y - x
            pct = (d / x * 100) if x else (0.0 if d == 0 else float("inf"))
            if a.only_changed and d == 0:
                continue
            if abs(pct) < a.min_pct:
                continue
            rows.append(f"| {k} | {x:,.2f} | {y:,.2f} | {d:+,.2f} | {pct:+.1f}% |")
        elif not a.only_changed:
            rows.append(f"| {k} | {'-' if x is None else f'{x:,.2f}'} | {'-' if y is None else f'{y:,.2f}'} | n/a | n/a |")
    out = "| metric | before | after | delta | delta % |\n|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    if a.out:
        Path(a.out).write_text(out)
        print(f"wrote {a.out} ({len(rows)} rows)")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
