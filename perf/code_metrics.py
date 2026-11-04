#!/usr/bin/env python3
"""Backend code-quality metrics (static + test run). Writes no caches into the repo.

    cd backend && uv run python ../perf/code_metrics.py              # -> docs/perf/raw/before/code.json
    cd backend && uv run python ../perf/code_metrics.py --skip-tests # static only (fast)

Collects: LOC per module, ruff C901 (max-complexity=10) findings, largest functions (ast),
duplicate-code hints (identical function bodies + repeated 6-line windows), counts of
`# type: ignore` / `except Exception` / `noqa`, ruff default-rule violations, mypy result + file count,
pytest count + wall time (+ slowest tests).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
APP = BACKEND / "app"
EXCLUDE = ("app/db/migrations/",)
WINDOW = 6


def py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if not any(e in str(p.relative_to(BACKEND)) for e in EXCLUDE))


def rel(p: Path) -> str:
    return str(p.relative_to(BACKEND))


def loc(path: Path) -> dict[str, int]:
    lines = path.read_text(encoding="utf-8").splitlines()
    code = [x for x in lines if x.strip() and not x.strip().startswith("#")]
    return {"lines": len(lines), "code": len(code)}


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str, str, float]:
    t0 = time.perf_counter()
    r = subprocess.run(cmd, cwd=BACKEND, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr, time.perf_counter() - t0


def complexity() -> dict[str, Any]:
    code, out, err, _ = run([
        "ruff", "check", "--no-cache", "--select", "C901", "--config", "lint.mccabe.max-complexity=10",
        "--output-format", "json", "app",
    ])
    items = json.loads(out or "[]")
    rows = []
    for it in items:
        m = re.search(r"`(.+?)` is too complex \((\d+) > \d+\)", it.get("message", ""))
        rows.append({
            "function": m.group(1) if m else "?",
            "complexity": int(m.group(2)) if m else None,
            "file": str(Path(it["filename"]).resolve().relative_to(BACKEND)),
            "line": it["location"]["row"],
        })
    rows.sort(key=lambda r: -(r["complexity"] or 0))
    return {"threshold": 10, "count": len(rows), "top": rows[:20], "all": rows}


def ruff_default() -> dict[str, Any]:
    code, out, err, dt = run(["ruff", "check", "--no-cache", "--output-format", "json", "app", "tests"])
    items = json.loads(out or "[]")
    by_rule: dict[str, int] = defaultdict(int)
    for it in items:
        by_rule[it.get("code") or "?"] += 1
    return {"violations": len(items), "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
            "seconds": round(dt, 2)}


def functions(files: list[Path]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    funcs: list[dict[str, Any]] = []
    bodies: dict[str, list[str]] = defaultdict(list)
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                n = (node.end_lineno or node.lineno) - node.lineno + 1
                funcs.append({"function": node.name, "file": rel(f), "line": node.lineno, "lines": n})
                if n >= 5:
                    body = node.body
                    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None),
                                                                               ast.Constant):
                        body = body[1:]  # ignore docstring
                    h = hashlib.sha1("".join(ast.dump(b) for b in body).encode()).hexdigest()
                    bodies[h].append(f"{rel(f)}:{node.lineno} {node.name}")
    funcs.sort(key=lambda x: -x["lines"])
    dup_bodies = [v for v in bodies.values() if len(v) > 1]
    return funcs, {"identical_function_bodies": dup_bodies}


def dup_windows(files: list[Path]) -> dict[str, Any]:
    """Repeated WINDOW-line blocks (whitespace-normalized, comments/blank/import/decorator lines skipped)."""
    seen: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for f in files:
        norm: list[tuple[int, str]] = []
        for i, raw in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            s = raw.strip()
            if not s or s.startswith(("#", "import ", "from ", "@", '"""', ")", "]", "}")) or len(s) < 4:
                continue
            norm.append((i, " ".join(s.split())))
        for k in range(len(norm) - WINDOW + 1):
            block = "\n".join(x[1] for x in norm[k:k + WINDOW])
            seen[hashlib.sha1(block.encode()).hexdigest()].append((rel(f), norm[k][0]))
    groups: dict[tuple[tuple[str, int], ...], int] = {}
    for locs in seen.values():
        uniq = sorted(set(locs))
        if len(uniq) > 1:
            groups[tuple(uniq)] = groups.get(tuple(uniq), 0) + 1
    # merge consecutive windows: keep first location per file-pair cluster
    clusters: list[dict[str, Any]] = []
    last: dict[tuple[str, ...], tuple[int, ...]] = {}
    for locs in sorted(groups):
        files_key = tuple(x[0] for x in locs)
        lines = tuple(x[1] for x in locs)
        prev = last.get(files_key)
        if prev and all(0 < b - a <= 3 for a, b in zip(prev, lines, strict=True)):
            clusters[-1]["windows"] += 1
        else:
            clusters.append({"locations": [f"{a}:{b}" for a, b in locs], "windows": 1})
        last[files_key] = lines
    clusters.sort(key=lambda c: -c["windows"])
    return {"window_lines": WINDOW, "duplicated_windows": len(groups), "clusters": len(clusters),
            "top": clusters[:15]}


def grep_counts(files: list[Path]) -> dict[str, int]:
    pats = {
        "type_ignore": r"#\s*type:\s*ignore",
        "except_exception": r"except\s+Exception\b",
        "bare_except": r"except\s*:",
        "noqa": r"#\s*noqa",
        "todo_fixme": r"\b(TODO|FIXME|XXX)\b",
        "private_access_engine_health": r"\._health\b",
    }
    out = dict.fromkeys(pats, 0)
    for f in files:
        txt = f.read_text(encoding="utf-8")
        for k, p in pats.items():
            out[k] += len(re.findall(p, txt))
    return out


def mypy() -> dict[str, Any]:
    code, out, err, dt = run(["mypy", "--cache-dir=/dev/null"], timeout=900)
    text = (out + err).strip().splitlines()
    last = text[-1] if text else ""
    m = re.search(r"in (\d+) source files?", last)
    errs = re.search(r"Found (\d+) errors?", last)
    return {"exit_code": code, "summary": last, "files_checked": int(m.group(1)) if m else None,
            "errors": int(errs.group(1)) if errs else 0, "seconds": round(dt, 1)}


def pytest_run() -> dict[str, Any]:
    code, out, err, dt = run([
        sys.executable, "-m", "pytest", "-q", "-p", "no:warnings", "-p", "no:cacheprovider", "-o", "addopts=",
        "--durations=15",
    ], timeout=1800)
    text = out + err
    summary = next((ln for ln in reversed(text.splitlines()) if re.search(r"\d+ (passed|failed)", ln)), "")
    counts = {k: int(v) for v, k in re.findall(r"(\d+) (passed|failed|skipped|errors?|xfailed|xpassed)", summary)}
    reported = re.search(r"in ([\d.]+)s", summary)
    slow = []
    in_dur = False
    for ln in text.splitlines():
        if "slowest" in ln and "durations" in ln:
            in_dur = True
            continue
        if in_dur:
            m = re.match(r"\s*([\d.]+)s (call|setup|teardown)\s+(.*)", ln)
            if m:
                slow.append({"seconds": float(m.group(1)), "phase": m.group(2), "test": m.group(3)})
            elif ln.strip() == "" or ln.startswith("="):
                if slow:
                    in_dur = False
    return {"exit_code": code, "summary": summary.strip("= "), "counts": counts,
            "total": sum(v for k, v in counts.items() if k in ("passed", "failed", "skipped", "error", "errors")),
            "pytest_reported_s": float(reported.group(1)) if reported else None,
            "wall_s": round(dt, 1), "slowest": slow[:15]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before", help="before | after | any tag")
    ap.add_argument("--out", default="", help="default: docs/perf/raw/<label>/code.json")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument("--skip-mypy", action="store_true")
    a = ap.parse_args()
    a.out = a.out or str(ROOT / "docs/perf/raw" / a.label / "code.json")
    app_files = py_files(APP)
    test_files = sorted((BACKEND / "tests").rglob("*.py"))
    per_module = sorted(({"file": rel(f), **loc(f)} for f in app_files), key=lambda x: -x["lines"])
    funcs, dup_bodies = functions(app_files)
    res: dict[str, Any] = {
        "schema": "code/v1",
        "taken_at": datetime.now(UTC).isoformat(),
        "app": {
            "files": len(app_files),
            "lines": sum(x["lines"] for x in per_module),
            "code_lines": sum(x["code"] for x in per_module),
            "top_modules": per_module[:20],
        },
        "tests_src": {"files": len(test_files), "lines": sum(loc(f)["lines"] for f in test_files)},
        "functions": {
            "count": len(funcs),
            "over_50_lines": sum(1 for f in funcs if f["lines"] > 50),
            "over_100_lines": sum(1 for f in funcs if f["lines"] > 100),
            "largest": funcs[:20],
        },
        "complexity_c901": complexity(),
        "duplicates": {**dup_bodies, "windows": dup_windows(app_files)},
        "grep_counts": grep_counts(app_files),
        "ruff_default_rules": ruff_default(),
    }
    print(f"app: {res['app']['files']} files, {res['app']['lines']} lines; "
          f"C901>10: {res['complexity_c901']['count']}")
    if not a.skip_mypy:
        res["mypy"] = mypy()
        print("mypy:", res["mypy"]["summary"])
    if not a.skip_tests:
        res["pytest"] = pytest_run()
        print("pytest:", res["pytest"]["summary"], f"(wall {res['pytest']['wall_s']}s)")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
