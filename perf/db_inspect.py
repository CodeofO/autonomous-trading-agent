#!/usr/bin/env python3
"""Live DB inspection — READ-ONLY.

* KR stack: Postgres service `db` (user/db `trader`) via `docker compose exec -T db psql ...`.
  Only SELECT / EXPLAIN (ANALYZE on SELECTs only) statements are sent; every psql session is opened
  with `default_transaction_read_only=on`.
* US stack: SQLite at /data/us_market.db inside the backend container. Copied out with
  `docker compose cp` into perf/tmp/ (git-ignored) and inspected there with PRAGMA query_only=ON.

Stdlib only. Run from the repo root:

    python3 perf/db_inspect.py                      # -> docs/perf/raw/before/db.json
    python3 perf/db_inspect.py --label after
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(__file__).resolve().parent / "tmp"

# Columns that application code filters / orders / joins on (cross-checked against app/**.py at baseline).
# (table, column, where it is used)
FILTER_COLUMNS: list[tuple[str, str, str]] = [
    ("orders", "created_at", "routes.py:253 orders since-filter; reports/daily.py:26 day range"),
    ("orders", "status", "routes.py:249, engine.py:166, orders.py:115/123/370"),
    ("orders", "symbol", "orders.py:123 last_order_times"),
    ("orders", "run_id", "container.py:198 purge_legacy; trades.py join"),
    ("orders", "broker_order_id", "orders.py:330/341 live order sync"),
    ("fills", "filled_at", "routes.py:270 since-filter; trades.py:40 ledger; reports/daily.py:24"),
    ("fills", "symbol", "routes.py:1300 candles markers"),
    ("fills", "order_id", "orders.py:230; trades.py:38 join"),
    ("agent_runs", "started_at", "routes.py:434/1529 since-filter; runner.py:290; reports/daily.py:36"),
    ("agent_runs", "status", "runner.py:288 memory_snapshot status='DONE'"),
    ("agent_decisions", "run_id", "routes.py:445; runner.py:297; reports/daily.py:39"),
    ("agent_decisions", "symbol", "routes.py:462/1303"),
    ("agent_decisions", "created_at", "routes.py:464 since-filter"),
    ("agent_decisions", "order_id", "container.py:203 purge_legacy"),
    ("chat_messages", "run_id", "routes.py:447 run detail"),
    ("chat_messages", "role", "runner.py:309/697 role filters (USER/ANALYST)"),
    ("chat_messages", "ts", "runner.py:696/716 today's chat"),
    ("audit_events", "type", "routes.py:927; reports/daily.py:31"),
    ("audit_events", "ts", "reports/daily.py:31; purge_legacy"),
    ("equity_snapshots", "ts", "routes.py:235 since-filter"),
    ("llm_usage", "ts", "costs.py:209 months window"),
    ("daily_prices", "symbol", "store.py:109 history/last_bar/name_of"),
    ("daily_prices", "day", "store.py:77/129 sync_day/snapshot"),
    ("memory_cards", "kind", "routes.py:1120 duplicate check"),
    ("memory_cards", "symbol", "routes.py:1120 duplicate check"),
    ("api_cache", "fetched_at", "(no filter in code; TTL checked in Python)"),
]

# Representative read queries used by hot endpoints / agent context, EXPLAIN ANALYZE'd (SELECT only).
EXPLAIN_QUERIES: dict[str, str] = {
    "chat_latest_200": "select * from chat_messages order by id desc limit 200",
    "events_latest_200": "select * from audit_events order by id desc limit 200",
    "orders_since_300": (
        "select * from orders where created_at >= now() - interval '365 days' order by id desc limit 300"
    ),
    "fills_since_200": (
        "select * from fills where filled_at >= now() - interval '365 days' order by id desc limit 200"
    ),
    "equity_since_1000": (
        "select * from equity_snapshots where ts >= now() - interval '365 days' order by id desc limit 1000"
    ),
    "runs_since_20": (
        "select * from agent_runs where started_at >= now() - interval '365 days' order by id desc limit 20"
    ),
    "memory_runs_done_3": "select * from agent_runs where status = 'DONE' order by id desc limit 3",
    "memory_user_msgs_5": "select * from chat_messages where role = 'user' order by id desc limit 5",
    "last_order_times": "select symbol, created_at from orders where status <> 'REJECTED'",
    "trade_ledger_all": (
        "select * from fills f join orders o on f.order_id = o.id order by f.filled_at, f.id"
    ),
    "name_of_last_bar": (
        "select * from daily_prices where symbol = (select symbol from daily_prices limit 1) "
        "order by day desc limit 1"
    ),
    "fills_by_symbol": "select * from fills where symbol = (select symbol from fills limit 1)",
    "llm_usage_12mo": "select * from llm_usage where ts >= now() - interval '365 days'",
}


# ---------------------------------------------------------------------------- Postgres
def psql(sql: str, db: str = "trader", user: str = "trader") -> str:
    cmd = [
        "docker", "compose", "exec", "-T",
        "-e", "PGOPTIONS=-c default_transaction_read_only=on",
        "db", "psql", "-U", user, "-d", db, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql,
    ]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr.strip()[:400]}")
    return r.stdout.strip()


def pg_json(sql: str) -> Any:
    out = psql(f"select coalesce(json_agg(t), '[]'::json) from ({sql}) t")
    return json.loads(out or "[]")


def lead_columns(indexdef: str) -> list[str]:
    m = re.search(r"\((.*)\)", indexdef)
    if not m:
        return []
    return [c.strip().strip('"').split(" ")[0] for c in m.group(1).split(",")]


def inspect_pg() -> dict[str, Any]:
    out: dict[str, Any] = {"engine": "postgresql"}
    out["version"] = psql("show server_version")
    out["database_bytes"] = int(psql("select pg_database_size(current_database())"))
    out["extensions"] = [r["extname"] for r in pg_json("select extname from pg_extension order by 1")]
    out["settings"] = {
        r["name"]: r["setting"]
        for r in pg_json(
            "select name, setting from pg_settings where name in "
            "('shared_buffers','work_mem','effective_cache_size','autovacuum','track_counts')"
        )
    }
    tables = pg_json(
        """
        select c.relname as table,
          (xpath('/row/cnt/text()', query_to_xml(format('select count(*) as cnt from public.%I', c.relname),
             false, true, '')))[1]::text::bigint as rows,
          pg_total_relation_size(c.oid) as total_bytes,
          pg_relation_size(c.oid) as heap_bytes,
          pg_indexes_size(c.oid) as index_bytes,
          coalesce(pg_total_relation_size(nullif(c.reltoastrelid, 0)), 0) as toast_bytes,
          s.n_live_tup, s.n_dead_tup, s.seq_scan, s.seq_tup_read, s.idx_scan,
          s.n_tup_ins, s.n_tup_upd, s.n_tup_del, s.n_tup_hot_upd,
          s.last_autovacuum, s.last_autoanalyze, s.autovacuum_count
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        left join pg_stat_user_tables s on s.relid = c.oid
        where n.nspname = 'public' and c.relkind = 'r'
        order by pg_total_relation_size(c.oid) desc
        """
    )
    out["tables"] = {t.pop("table"): t for t in tables}
    idx = pg_json(
        """
        select s.relname as table, s.indexrelname as index, pg_relation_size(s.indexrelid) as bytes,
               s.idx_scan, s.idx_tup_read, i.indexdef
        from pg_stat_user_indexes s
        join pg_indexes i on i.indexname = s.indexrelname and i.schemaname = s.schemaname
        order by s.relname, s.indexrelname
        """
    )
    out["indexes"] = idx
    # redundant: single-column index whose column is the leading column of another (multi-col/unique) index
    redundant = []
    by_table: dict[str, list[dict[str, Any]]] = {}
    for i in idx:
        by_table.setdefault(i["table"], []).append(i)
    for tbl, items in by_table.items():
        for a in items:
            ca = lead_columns(a["indexdef"])
            for b in items:
                cb = lead_columns(b["indexdef"])
                if a is not b and len(ca) == 1 and len(cb) > 1 and cb[0] == ca[0]:
                    redundant.append({"table": tbl, "index": a["index"], "covered_by": b["index"]})
    out["redundant_indexes"] = redundant

    # JSON / TEXT column payload sizes
    cols = pg_json(
        "select table_name, column_name, data_type from information_schema.columns "
        "where table_schema='public' and data_type in ('json','jsonb','text') order by 1, 2"
    )
    sizes = {}
    for c in cols:
        t, col = c["table_name"], c["column_name"]
        r = pg_json(
            f'select count(*) as n, round(avg(length("{col}"::text))) as avg_len, '
            f'max(length("{col}"::text)) as max_len, sum(length("{col}"::text)) as sum_len, '
            f'round(avg(pg_column_size("{col}"))) as avg_stored, '
            f'percentile_disc(0.95) within group (order by length("{col}"::text)) as p95_len '
            f'from public."{t}"'
        )[0]
        sizes[f"{t}.{col}"] = {"type": c["data_type"], **r}
    out["column_sizes"] = dict(sorted(sizes.items(), key=lambda kv: -(kv[1]["sum_len"] or 0)))

    # breakdowns that matter for cleanup decisions (lengths only, never values)
    out["breakdowns"] = {
        "audit_events_by_type": pg_json(
            "select type, count(*) as n, round(avg(length(payload::text))) as avg_payload, "
            "max(length(payload::text)) as max_payload, round(avg(length(message))) as avg_message "
            "from audit_events group by type order by n desc"
        ),
        "chat_messages_by_role_kind": pg_json(
            "select role, kind, count(*) as n, round(avg(length(payload::text))) as avg_payload, "
            "max(length(payload::text)) as max_payload, round(avg(length(content))) as avg_content "
            "from chat_messages group by role, kind order by n desc"
        ),
        "api_cache_by_prefix": pg_json(
            "select split_part(key, ':', 1) as prefix, count(*) as n, "
            "round(avg(length(payload::text))) as avg_payload, max(length(payload::text)) as max_payload, "
            "min(fetched_at) as oldest, max(fetched_at) as newest from api_cache group by 1 order by n desc"
        ),
        "app_settings_value_sizes": pg_json(
            "select key, length(value::text) as bytes, updated_at from app_settings order by 2 desc"
        ),
        "paper_account_state": pg_json(
            "select id, length(state::text) as bytes, "
            "(select count(*) from json_object_keys(state->'orders')) as orders, "
            "(select count(*) from json_object_keys(state->'client_ids')) as client_ids, "
            "(select count(*) from json_object_keys(state->'positions')) as positions "
            "from paper_account"
        ),
        "agent_runs_usage": pg_json(
            "select count(*) as n, round(avg(length(usage::text))) as avg_usage, "
            "max(length(usage::text)) as max_usage from agent_runs"
        ),
        "time_ranges": pg_json(
            "select 'chat_messages' as tbl, min(ts) as oldest, max(ts) as newest from chat_messages union all "
            "select 'audit_events', min(ts), max(ts) from audit_events union all "
            "select 'equity_snapshots', min(ts), max(ts) from equity_snapshots union all "
            "select 'agent_runs', min(started_at), max(started_at) from agent_runs union all "
            "select 'daily_prices', min(day)::timestamptz, max(day)::timestamptz from daily_prices union all "
            "select 'llm_usage', min(ts), max(ts) from llm_usage"
        ),
        "daily_prices_symbols": pg_json(
            "select count(distinct symbol) as symbols, count(*) as rows, "
            "round(count(*)::numeric / nullif(count(distinct symbol), 0)) as rows_per_symbol from daily_prices"
        ),
        "equity_snapshots_per_kst_day": pg_json(
            "select (ts at time zone 'Asia/Seoul')::date as day, count(*) as n, "
            "count(*) filter (where extract(hour from ts at time zone 'Asia/Seoul') not between 9 and 15) "
            "as outside_kr_session from equity_snapshots group by 1 order by 1"
        ),
        "chat_messages_per_kst_day": pg_json(
            "select (ts at time zone 'Asia/Seoul')::date as day, count(*) as n from chat_messages "
            "group by 1 order by 1"
        ),
        "account_since": pg_json(
            "select value->>'since' as since, value->>'source' as source from app_settings "
            "where key = 'account_source'"
        ),
    }

    # missing-index check for filter columns used in code
    have = {(i["table"], lead_columns(i["indexdef"])[0]) for i in idx if lead_columns(i["indexdef"])}
    out["filter_columns"] = [
        {
            "table": t, "column": c, "used_in": where, "indexed": (t, c) in have,
            "rows": out["tables"].get(t, {}).get("rows"),
        }
        for t, c, where in FILTER_COLUMNS
    ]
    out["missing_indexes"] = [f for f in out["filter_columns"] if not f["indexed"]]

    plans = {}
    for name, q in EXPLAIN_QUERIES.items():
        try:
            raw = psql(f"explain (analyze, buffers, format json) {q}")
            p = json.loads(raw)[0]
            nodes: list[str] = []

            def walk(n: dict[str, Any]) -> None:
                label = n["Node Type"] + (f" on {n['Relation Name']}" if "Relation Name" in n else "")
                if "Index Name" in n:
                    label += f" using {n['Index Name']}"
                nodes.append(label)
                for ch in n.get("Plans", []):
                    walk(ch)

            walk(p["Plan"])
            plans[name] = {
                "execution_ms": p.get("Execution Time"),
                "planning_ms": p.get("Planning Time"),
                "rows": p["Plan"].get("Actual Rows"),
                "shared_hit": p["Plan"].get("Shared Hit Blocks"),
                "shared_read": p["Plan"].get("Shared Read Blocks"),
                "nodes": nodes,
            }
        except Exception as e:  # noqa: BLE001
            plans[name] = {"error": str(e)[:200]}
    out["explain"] = plans
    return out


# ---------------------------------------------------------------------------- SQLite (US)
def copy_us_db(container_path: str) -> Path:
    TMP.mkdir(parents=True, exist_ok=True)
    dest_dir = TMP / "us_db_copy"
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    dest_dir.mkdir()
    for suffix in ("", "-wal", "-shm"):
        r = subprocess.run(
            ["docker", "compose", "cp", f"backend:{container_path}{suffix}", str(dest_dir / f"us_market.db{suffix}")],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0 and suffix == "":
            raise RuntimeError(f"docker compose cp failed: {r.stderr.strip()[:300]}")
    return dest_dir / "us_market.db"


def inspect_sqlite(path: Path) -> dict[str, Any]:
    con = sqlite3.connect(str(path))
    con.execute("PRAGMA query_only=ON")
    cur = con.cursor()
    out: dict[str, Any] = {"engine": "sqlite", "file_bytes": {}}
    for f in path.parent.iterdir():
        out["file_bytes"][f.name] = f.stat().st_size
    page_size = cur.execute("PRAGMA page_size").fetchone()[0]
    out["page_size"] = page_size
    out["page_count"] = cur.execute("PRAGMA page_count").fetchone()[0]
    out["freelist_count"] = cur.execute("PRAGMA freelist_count").fetchone()[0]
    out["journal_mode"] = cur.execute("PRAGMA journal_mode").fetchone()[0]
    tables = [r[0] for r in cur.execute(
        "select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name")]
    dbstat: dict[str, int] = {}
    try:
        for name, size in cur.execute("select name, sum(pgsize) from dbstat group by name"):
            dbstat[name] = size
        out["dbstat_available"] = True
    except sqlite3.Error:
        out["dbstat_available"] = False
    idx_rows = cur.execute(
        "select name, tbl_name, sql from sqlite_master where type='index' order by tbl_name, name").fetchall()
    out["tables"] = {}
    for t in tables:
        n = cur.execute(f'select count(*) from "{t}"').fetchone()[0]
        tbl_idx = [i for i in idx_rows if i[1] == t]
        out["tables"][t] = {
            "rows": n,
            "table_bytes": dbstat.get(t),
            "index_bytes": sum(dbstat.get(i[0], 0) for i in tbl_idx) if dbstat else None,
        }
    out["indexes"] = []
    have = set()
    for name, tbl, sql in idx_rows:
        cols = [r[2] for r in cur.execute(f'PRAGMA index_info("{name}")')]
        out["indexes"].append({"table": tbl, "index": name, "columns": cols, "bytes": dbstat.get(name),
                               "auto": sql is None})
        if cols:
            have.add((tbl, cols[0]))
    # JSON / TEXT columns
    sizes = {}
    for t in tables:
        for _cid, col, ctype, *_ in cur.execute(f'PRAGMA table_info("{t}")').fetchall():
            if ctype.upper() in ("JSON", "TEXT"):
                n, avg_len, max_len, sum_len = cur.execute(
                    f'select count(*), round(avg(length("{col}"))), max(length("{col}")), '
                    f'sum(length("{col}")) from "{t}"').fetchone()
                sizes[f"{t}.{col}"] = {"type": ctype, "n": n, "avg_len": avg_len, "max_len": max_len,
                                       "sum_len": sum_len}
    out["column_sizes"] = dict(sorted(sizes.items(), key=lambda kv: -(kv[1]["sum_len"] or 0)))
    out["filter_columns"] = [
        {"table": t, "column": c, "used_in": w, "indexed": (t, c) in have,
         "rows": out["tables"].get(t, {}).get("rows")}
        for t, c, w in FILTER_COLUMNS if t in out["tables"]
    ]
    out["missing_indexes"] = [f for f in out["filter_columns"] if not f["indexed"]]
    try:
        row = cur.execute(
            "select length(state), (select count(*) from json_each(state, '$.orders')), "
            "(select count(*) from json_each(state, '$.positions')) from paper_account").fetchone()
        out["paper_account_state"] = {"bytes": row[0], "orders": row[1], "positions": row[2]} if row else None
    except sqlite3.Error as e:
        out["paper_account_state"] = {"error": str(e)}
    out["app_settings_value_sizes"] = [
        {"key": k, "bytes": b} for k, b in
        cur.execute("select key, length(value) from app_settings order by 2 desc").fetchall()
    ]
    con.close()
    return out


# ---------------------------------------------------------------------------- live activity sample
def _activity_snapshot() -> dict[str, Any]:
    db = pg_json(
        "select xact_commit, xact_rollback, tup_returned, tup_fetched, tup_inserted, tup_updated, tup_deleted "
        "from pg_stat_database where datname = current_database()"
    )[0]
    tables = {
        r["relname"]: r
        for r in pg_json(
            "select relname, seq_scan, coalesce(idx_scan, 0) as idx_scan, n_tup_ins, n_tup_upd, n_tup_del "
            "from pg_stat_user_tables"
        )
    }
    return {"db": db, "tables": tables, "t": time.time()}


def sample_activity(seconds: int, api: str) -> dict[str, Any]:
    """Background DB load: pg_stat deltas over `seconds` (no requests are sent by this script meanwhile).

    Includes whatever else hits the server during the window (scheduler jobs, open browser tabs).
    """
    state = {}
    try:
        with urllib.request.urlopen(f"{api}/api/markets", timeout=5) as r:  # one GET, before the window
            state = {m["code"]: {"open_now": m.get("open_now"), "agent_running": m.get("agent_running")}
                     for m in json.loads(r.read())}
    except Exception as e:  # noqa: BLE001
        state = {"error": type(e).__name__}
    a = _activity_snapshot()
    time.sleep(seconds)
    b = _activity_snapshot()
    per_min = 60.0 / (b["t"] - a["t"])
    out: dict[str, Any] = {
        "seconds": round(b["t"] - a["t"], 1),
        "server_state": state,
        "per_minute": {k: round((b["db"][k] - a["db"][k]) * per_min, 1) for k in a["db"]},
        "tables_per_minute": {},
    }
    for t, rb in b["tables"].items():
        ra = a["tables"].get(t)
        if not ra:
            continue
        d = {k: round((rb[k] - ra[k]) * per_min, 1) for k in ("seq_scan", "idx_scan", "n_tup_ins", "n_tup_upd")}
        if any(d.values()):
            out["tables_per_minute"][t] = d
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before", help="before | after | any tag")
    ap.add_argument("--out", default="", help="default: docs/perf/raw/<label>/db.json")
    ap.add_argument("--us-path", default="/data/us_market.db")
    ap.add_argument("--skip-us", action="store_true")
    ap.add_argument("--sample-seconds", type=int, default=60,
                    help="measure background DB activity (pg_stat deltas) over N seconds; 0 = skip")
    ap.add_argument("--api", default="http://127.0.0.1:18000", help="only used for one GET /api/markets")
    a = ap.parse_args()
    a.out = a.out or str(ROOT / "docs/perf/raw" / a.label / "db.json")
    res: dict[str, Any] = {"schema": "db/v1", "taken_at": datetime.now(UTC).isoformat()}
    try:
        res["kr_postgres"] = inspect_pg()
        if a.sample_seconds > 0:
            res["kr_postgres"]["activity"] = sample_activity(a.sample_seconds, a.api)
    except Exception as e:  # noqa: BLE001
        res["kr_postgres"] = {"error": str(e)}
        print("postgres inspection failed:", e, file=sys.stderr)
    if not a.skip_us:
        try:
            res["us_sqlite"] = inspect_sqlite(copy_us_db(a.us_path))
        except Exception as e:  # noqa: BLE001
            res["us_sqlite"] = {"error": str(e)}
            print("sqlite inspection failed:", e, file=sys.stderr)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    kr = res.get("kr_postgres", {})
    for t, v in (kr.get("tables") or {}).items():
        print(f"{t:20s} rows={v['rows']:>8} total={v['total_bytes']:>10} dead={v['n_dead_tup']}")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
