#!/usr/bin/env python3
"""Live API latency against the running docker stack — GET ONLY, sequential, rate-limited.

Safe to run while a paper-trading session is active: it never sends POST/PUT/PATCH/DELETE,
sends one request at a time and keeps the request rate <= 1/--min-interval (default 5 req/s).
Needs only the Python standard library.

    python3 perf/bench_live_api.py                       # -> docs/perf/raw/before/live_api.json
    python3 perf/bench_live_api.py --label after

Auth: if /api/auth/status says auth is enabled, pass a session token with --token-env NAME
(the token is read from that environment variable, never printed or stored).
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import os
import platform
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from endpoints import ALL_ENDPOINTS, count_items, percentile  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_METHOD = "GET"  # hard-coded: this script must never mutate server state


class Client:
    def __init__(self, base: str, min_interval: float, token: str | None) -> None:
        u = urlsplit(base)
        self.host, self.port = u.hostname or "127.0.0.1", u.port or 80
        self.min_interval = min_interval
        self.headers = {"Accept": "application/json", "Connection": "keep-alive"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
        self._last = 0.0
        self.total_requests = 0

    def get(self, path: str) -> tuple[int, bytes, float]:
        wait = self.min_interval - (time.perf_counter() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.perf_counter()
        for attempt in range(2):
            try:
                t0 = time.perf_counter()
                self.conn.request(ALLOWED_METHOD, path, headers=self.headers)
                r = self.conn.getresponse()
                body = r.read()
                dt = (time.perf_counter() - t0) * 1000
                self.total_requests += 1
                return r.status, body, dt
            except (http.client.HTTPException, OSError):
                self.conn.close()
                self.conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
                if attempt:
                    raise
        raise RuntimeError("unreachable")


def state(c: Client) -> dict[str, Any]:
    """Snapshot of server state that can skew latency (agent session running, market open)."""
    out: dict[str, Any] = {}
    try:
        st, body, _ = c.get("/api/markets")
        if st == 200:
            for m in json.loads(body):
                out[m["code"]] = {
                    "enabled": m.get("enabled"),
                    "open_now": m.get("open_now"),
                    "agent_running": m.get("agent_running"),
                    "engine_status": m.get("engine_status"),
                }
    except Exception as e:  # noqa: BLE001
        out["error"] = type(e).__name__
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18000")
    ap.add_argument("--n", type=int, default=15, help="measured requests per endpoint")
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--min-interval", type=float, default=0.2, help="seconds between request starts (>=0.2)")
    ap.add_argument("--only", default="", help="comma-separated endpoint keys")
    ap.add_argument("--token-env", default="", help="env var holding a bearer token (if auth is enabled)")
    ap.add_argument("--label", default="before", help="before | after | any tag")
    ap.add_argument("--out", default="", help="default: docs/perf/raw/<label>/live_api.json")
    ap.add_argument("--require-idle", action="store_true",
                    help="abort (exit 3) if an agent session is running when the run starts")
    a = ap.parse_args()
    a.out = a.out or str(ROOT / "docs/perf/raw" / a.label / "live_api.json")
    if a.min_interval < 0.2:
        ap.error("--min-interval must be >= 0.2 (<= 5 req/s against the live server)")
    token = os.environ.get(a.token_env) if a.token_env else None
    c = Client(a.base, a.min_interval, token)

    st, body, _ = c.get("/api/auth/status")
    auth = json.loads(body) if st == 200 else {"error": st}
    if auth.get("auth_enabled") and not token:
        print("auth is enabled and no token given (--token-env); aborting live measurement", file=sys.stderr)
        return 2

    started = datetime.now(UTC)
    before = state(c)
    busy = [k for k, v in before.items() if isinstance(v, dict) and v.get("agent_running")]
    if a.require_idle and busy:
        print(f"agent session running on {busy}; re-run later (or drop --require-idle)", file=sys.stderr)
        return 3
    only = {k for k in a.only.split(",") if k}
    results: dict[str, Any] = {}
    for key, path, source, note in ALL_ENDPOINTS:
        if only and key not in only:
            continue
        for _ in range(a.warmup):
            c.get(path)
        times: list[float] = []
        sizes: list[int] = []
        statuses: dict[str, int] = {}
        items = None
        data = b""
        for _ in range(a.n):
            status, data, ms = c.get(path)
            times.append(ms)
            sizes.append(len(data))
            statuses[str(status)] = statuses.get(str(status), 0) + 1
            if status == 200 and items is None:
                try:
                    items = count_items(json.loads(data))
                except ValueError:
                    pass
        results[key] = {
            "path": path,
            "source": source,
            "note": note,
            "n": a.n,
            "p50_ms": round(percentile(times, 50), 2),
            "p95_ms": round(percentile(times, 95), 2),
            "max_ms": round(max(times), 2),
            "min_ms": round(min(times), 2),
            "mean_ms": round(statistics.fmean(times), 2),
            "bytes": int(statistics.median(sizes)),
            "bytes_gzip": len(gzip.compress(data, 6)),
            "items": items,
            "status": statuses,
        }
        r = results[key]
        print(f"{key:24s} p50={r['p50_ms']:8.1f} p95={r['p95_ms']:8.1f} max={r['max_ms']:8.1f} "
              f"bytes={r['bytes']:>9d} gz={r['bytes_gzip']:>8d} items={r['items']} {statuses}")
    after = state(c)
    out = {
        "schema": "live_api/v1",
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "base": a.base,
        "auth": auth,
        "params": {"n": a.n, "warmup": a.warmup, "min_interval_s": a.min_interval},
        "client": {"python": platform.python_version(), "platform": platform.platform()},
        "server_state_before": before,
        "server_state_after": after,
        "total_requests": c.total_requests,
        "endpoints": results,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"wrote {a.out} ({c.total_requests} GET requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
