#!/usr/bin/env node
// Compare two merged frontend reports (BEFORE vs AFTER) and print a Markdown diff table.
//
//   node perf/fe_compare.mjs                                    # baseline-frontend.json vs after-frontend.json
//   node perf/fe_compare.mjs A.json B.json --only-changed --min-pct 5 --out docs/perf/compare-frontend.md
//   node perf/fe_compare.mjs --headline-only                    # just the flat `headline` block
//
// Every numeric leaf is flattened to a dotted key (arrays of objects are keyed by name/file/at/url);
// raw per-run arrays are skipped. Lower is better for almost everything (time, bytes, requests, renders);
// the few "higher is better" keys (tests, fps, frames) are marked.

import fs from "node:fs";
import path from "node:path";
import { ROOT, parseArgs } from "./fe_lib.mjs";

const pos = process.argv.slice(2).filter((a, i, all) => !a.startsWith("--") && !(all[i - 1] ?? "").match(/^--(min-pct|out)$/));
const args = parseArgs(process.argv.slice(2), { min_pct: "0", out: "" });
const A = pos[0] ?? path.join(ROOT, "docs/perf/baseline-frontend.json");
const B = pos[1] ?? path.join(ROOT, "docs/perf/after-frontend.json");
const SKIP = new Set(["all_runs", "queries", "window_examples", "git", "generated_at", "label", "server_state", "server_state_before", "server_state_after", "slowest_files", "files", "warnings", "network"]);

function flatten(o, prefix, out) {
  if (o == null) return out;
  if (typeof o === "number" && Number.isFinite(o)) out[prefix] = o;
  else if (typeof o === "boolean") out[prefix] = o ? 1 : 0;
  else if (Array.isArray(o)) {
    o.forEach((x, i) => {
      if (x && typeof x === "object" && !Array.isArray(x)) {
        const k = x.name ?? x.file ?? x.url ?? x.at ?? i;
        flatten(x, `${prefix}[${k}]`, out);
      }
    });
  } else if (typeof o === "object") {
    for (const [k, v] of Object.entries(o)) if (!SKIP.has(k)) flatten(v, prefix ? `${prefix}.${k}` : k, out);
  }
  return out;
}

const a = JSON.parse(fs.readFileSync(A, "utf8"));
const b = JSON.parse(fs.readFileSync(B, "utf8"));
const fa = flatten(args.headline_only ? a.headline : a, "", {});
const fb = flatten(args.headline_only ? b.headline : b, "", {});
const keys = [...new Set([...Object.keys(fa), ...Object.keys(fb)])].sort();
const higherBetter = (k) => /tests_total|tests_passed|avg_fps|\.frames$|_frames$|routes_lazy|js_chunks|dynamic|react_memo|served_js_compressed/.test(k);
const minPct = Number(args.min_pct);
const rows = [];
for (const k of keys) {
  const x = fa[k],
    y = fb[k];
  if (x === undefined || y === undefined) {
    if (!args.only_changed) rows.push([k, x ?? "—", y ?? "—", "", "", ""]);
    continue;
  }
  const d = y - x;
  const p = x === 0 ? (y === 0 ? 0 : Infinity) : (d / Math.abs(x)) * 100;
  if (args.only_changed && d === 0) continue;
  if (Number.isFinite(p) && Math.abs(p) < minPct) continue;
  const good = d === 0 ? "" : (d < 0) !== higherBetter(k) ? "better" : "worse";
  rows.push([k, +x.toFixed(2), +y.toFixed(2), +d.toFixed(2), Number.isFinite(p) ? `${p.toFixed(1)}%` : "new", good]);
}
const md = [
  `# Frontend BEFORE vs AFTER`,
  "",
  `\`${path.relative(ROOT, A)}\` (${a.generated_at}) vs \`${path.relative(ROOT, B)}\` (${b.generated_at}). ${rows.length} rows.`,
  "",
  "| metric | before | after | delta | delta % | |",
  "|---|---|---|---|---|---|",
  ...rows.map((r) => `| ${r.join(" | ")} |`),
  "",
].join("\n");
if (args.out) {
  fs.writeFileSync(path.resolve(args.out), md);
  console.log(`wrote ${args.out}`);
} else process.stdout.write(md);
