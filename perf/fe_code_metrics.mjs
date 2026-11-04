#!/usr/bin/env node
// Frontend code metrics: LOC, largest files/components, React Query usage (refetchInterval / staleTime),
// hook counts, inline styles, `any` / `as` casts, duplicated helpers, CSS rules, eslint / prettier / tsc,
// vitest count + runtime. Read-only on the source tree (tsc writes only its tsbuildinfo under node_modules).
//
//   node perf/fe_code_metrics.mjs [--label before] [--skip-tests] [--skip-lint]
//   -> docs/perf/raw/<label>/fe_code.json

import { execSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { FRONTEND, TMP, gitInfo, parseArgs, rawDir, round, writeJson } from "./fe_lib.mjs";

const args = parseArgs(process.argv.slice(2), { label: "before" });
const SRC = path.join(FRONTEND, "src");
const rel = (p) => path.relative(FRONTEND, p).split(path.sep).join("/");

const files = [];
const walk = (d) =>
  fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(tsx?|css)$/.test(e.name)) files.push(p);
  });
walk(SRC);
const isTest = (p) => /__tests__|\.test\.|\/src\/test\//.test(p);
const kind = (p) => (p.endsWith(".css") ? "css" : isTest(p) ? "test" : "app");
const text = Object.fromEntries(files.map((f) => [f, fs.readFileSync(f, "utf8")]));

// ---------- LOC ----------
const loc = files.map((f) => {
  const lines = text[f].split("\n");
  if (lines.at(-1) === "") lines.pop();
  return { file: rel(f), kind: kind(f), lines: lines.length, nonblank: lines.filter((l) => l.trim()).length, bytes: Buffer.byteLength(text[f]) };
});
const totals = {};
for (const k of ["app", "test", "css"]) {
  const xs = loc.filter((x) => x.kind === k);
  totals[k] = { files: xs.length, lines: xs.reduce((s, x) => s + x.lines, 0), nonblank: xs.reduce((s, x) => s + x.nonblank, 0) };
}
const top20 = [...loc].sort((a, b) => b.lines - a.lines).slice(0, 20).map(({ file, kind, lines }) => ({ file, kind, lines }));

const appFiles = files.filter((f) => kind(f) === "app");
const tsxApp = appFiles.filter((f) => f.endsWith(".tsx"));

// ---------- components (top-level function/const with Capitalized name) ----------
const TOP = /^(export\s+)?(default\s+)?(async\s+)?(function|const|let|class|interface|type|enum)\s+/;
const components = [];
for (const f of tsxApp) {
  const lines = text[f].split("\n");
  const starts = [];
  lines.forEach((l, i) => {
    if (TOP.test(l) || /^\/\*\*|^\/\//.test(l)) starts.push(i);
  });
  lines.forEach((l, i) => {
    const m = l.match(/^(?:export\s+)?(?:default\s+)?function\s+([A-Z]\w*)\s*[(<]/) ?? l.match(/^(?:export\s+)?const\s+([A-Z]\w*)\s*=\s*(?:memo|forwardRef|\()/);
    if (!m) return;
    const next = starts.find((s) => s > i && !/^\/\*\*|^\/\//.test(lines[s])) ?? lines.length;
    let end = next;
    while (end > i && (!lines[end - 1].trim() || /^\s*(\/\/|\/\*\*|\*)/.test(lines[end - 1]))) end--;
    components.push({ name: m[1], file: rel(f), line: i + 1, lines: end - i });
  });
}
components.sort((a, b) => b.lines - a.lines);

// ---------- hooks / React Query ----------
const count = (re, fs_ = appFiles) => fs_.reduce((s, f) => s + (text[f].match(re) ?? []).length, 0);
const hooks = {
  useQuery: count(/\buseQuery\s*\(/g),
  useQueries: count(/\buseQueries\s*\(/g),
  useMutation: count(/\buseMutation\s*\(/g),
  useEffect: count(/\buseEffect\s*\(/g),
  useLayoutEffect: count(/\buseLayoutEffect\s*\(/g),
  useState: count(/\buseState\s*[(<]/g),
  useMemo: count(/\buseMemo\s*[(<]/g),
  useCallback: count(/\buseCallback\s*[(<]/g),
  react_memo: count(/\b(React\.)?memo\s*\(/g),
  react_lazy: count(/\blazy\s*\(/g),
  invalidateQueries: count(/\binvalidateQueries\s*\(/g),
  invalidateQueries_all: count(/\binvalidateQueries\(\s*\)/g),
  setInterval: count(/\bsetInterval\s*\(/g),
  websocket: count(/new\s+WebSocket\s*\(/g),
};
function objectAt(src, start) {
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  return "";
}
const queries = [];
for (const f of appFiles) {
  const src = text[f];
  for (const m of src.matchAll(/\buseQuery\s*\(\s*\{/g)) {
    const obj = objectAt(src, m.index + m[0].length - 1);
    const field = (k) => {
      const mm = obj.match(new RegExp(`\\b${k}\\s*:\\s*([^,\\n]+?)\\s*,?\\s*(?:\\n|\\}$)`));
      return mm ? mm[1].trim() : null;
    };
    const line = src.slice(0, m.index).split("\n").length;
    queries.push({
      at: `${rel(f)}:${line}`,
      queryKey: (obj.match(/queryKey\s*:\s*([^\n]+?),?\n/) ?? obj.match(/queryKey\s*:\s*([^,}]+)/) ?? [])[1]?.trim() ?? null,
      endpoint: (obj.match(/["'`](\/api\/[^"'`?$]*)/) ?? [])[1] ?? null,
      refetchInterval: field("refetchInterval"),
      staleTime: field("staleTime"),
      enabled: field("enabled"),
    });
  }
}
const appTsx = text[path.join(SRC, "App.tsx")] ?? "";
const qcDefaultsAt = appTsx.search(/defaultOptions:\s*\{/);
const qcDefaults = qcDefaultsAt < 0 ? null : objectAt(appTsx, appTsx.indexOf("{", qcDefaultsAt));
const polling = queries.filter((q) => q.refetchInterval);

// ---------- casts / any / inline styles / misc ----------
function castStats(fs_) {
  let asCasts = 0,
    asConst = 0,
    asUnknownAs = 0,
    anyCount = 0,
    nonNull = 0,
    tsIgnore = 0,
    eslintDisable = 0;
  for (const f of fs_) {
    for (const raw of text[f].split("\n")) {
      const l = raw.replace(/\/\/.*$/, "").replace(/(["'`])(?:\\.|(?!\1).)*\1/g, '""');
      if (/^\s*(import|export)\s.*\sfrom\s/.test(raw) || /^\s*import\s/.test(raw)) continue;
      asConst += (l.match(/\bas\s+const\b/g) ?? []).length;
      asUnknownAs += (l.match(/\bas\s+unknown\s+as\b/g) ?? []).length;
      asCasts += (l.match(/[\w)\]}>"]\s+as\s+(?!const\b)[A-Za-z_{[(]/g) ?? []).length;
      anyCount += (l.match(/(:\s*any\b|<any\b|\bas\s+any\b|\bany\[\]|,\s*any\s*>)/g) ?? []).length;
      nonNull += (l.match(/[\w)\]]!(?=[.[)])/g) ?? []).length;
    }
    tsIgnore += (text[f].match(/@ts-(ignore|expect-error|nocheck)/g) ?? []).length;
    eslintDisable += (text[f].match(/eslint-disable/g) ?? []).length;
  }
  return { as_casts: asCasts, as_const: asConst, as_unknown_as: asUnknownAs, any: anyCount, non_null_assertions: nonNull, ts_ignore: tsIgnore, eslint_disable: eslintDisable };
}
const inlineStyles = {};
for (const f of tsxApp) {
  const n = (text[f].match(/\bstyle=\{\{/g) ?? []).length;
  const n2 = (text[f].match(/\bstyle=\{(?!\{)/g) ?? []).length;
  if (n + n2) inlineStyles[rel(f)] = { object_literal: n, expression: n2 };
}
const inlineTotal = Object.values(inlineStyles).reduce((s, v) => s + v.object_literal + v.expression, 0);
const indexKeys = count(/\bkey=\{(i|idx|index)\}/g, tsxApp);
const nowInRender = count(/new Date\(\)|Date\.now\(\)/g, tsxApp);

// ---------- duplicated helpers ----------
const defs = new Map();
for (const f of appFiles) {
  for (const m of text[f].matchAll(/^(?:export\s+)?(?:function|const)\s+([A-Za-z_]\w*)/gm)) {
    const name = m[1];
    if (!defs.has(name)) defs.set(name, []);
    defs.get(name).push(`${rel(f)}:${text[f].slice(0, m.index).split("\n").length}`);
  }
}
const dupNames = [...defs.entries()].filter(([, v]) => v.length > 1).map(([name, at]) => ({ name, at }));
const fmtOutside = [...defs.entries()]
  .filter(([n]) => /^(fmt|format|money|price|pct|won|usd)/i.test(n))
  .flatMap(([name, at]) => at.filter((a) => !a.startsWith("src/utils/")).map((a) => ({ name, at: a })));
// 6-line windows of normalized, non-trivial code that appear in more than one place
const WIN = 6;
const windows = new Map();
for (const f of appFiles.filter((x) => !x.endsWith(".css"))) {
  const lines = text[f].split("\n").map((l, i) => [l.trim(), i + 1]).filter(([l]) => l.length > 3 && !/^[})\]];?,?$/.test(l) && !/^(import|\/\/|\*|\/\*\*)/.test(l));
  for (let i = 0; i + WIN <= lines.length; i++) {
    const h = crypto.createHash("sha1").update(lines.slice(i, i + WIN).map((x) => x[0]).join("\n")).digest("hex");
    if (!windows.has(h)) windows.set(h, []);
    windows.get(h).push(`${rel(f)}:${lines[i][1]}`);
  }
}
const dupWindows = [...windows.values()].filter((v) => v.length > 1 && new Set(v.map((x) => x.split(":")[0])).size >= 1);
const dupExamples = [];
const seen = new Set();
for (const v of dupWindows) {
  const k = v.map((x) => x.split(":")[0]).sort().join("|");
  if (seen.has(k)) continue;
  seen.add(k);
  dupExamples.push(v);
}

// ---------- CSS ----------
function cssStats(src) {
  const s = src.replace(/\/\*[\s\S]*?\*\//g, "");
  const stack = [];
  let buf = "";
  const st = { style_rules: 0, selectors: 0, declarations: 0, media_queries: 0, keyframes: 0, supports: 0, other_at_rules: 0, important: 0, max_nesting: 0 };
  const props = new Set();
  for (const ch of s) {
    if (ch === "{") {
      const pre = buf.trim();
      buf = "";
      if (pre.startsWith("@")) {
        if (/^@media/.test(pre)) st.media_queries++;
        else if (/^@(-webkit-)?keyframes/.test(pre)) st.keyframes++;
        else if (/^@supports/.test(pre)) st.supports++;
        else st.other_at_rules++;
        stack.push(/keyframes/.test(pre) ? "kf" : "at");
      } else if (stack.at(-1) === "kf") stack.push("kfsel");
      else {
        st.style_rules++;
        st.selectors += pre.split(",").length;
        stack.push("rule");
      }
      st.max_nesting = Math.max(st.max_nesting, stack.length);
    } else if (ch === "}") {
      const top = stack.pop();
      if (top === "rule" || top === "kfsel") {
        const decls = buf.split(";").map((d) => d.trim()).filter(Boolean);
        if (top === "rule") st.declarations += decls.length;
        for (const d of decls) {
          if (/!important/.test(d)) st.important++;
          const pm = d.match(/^(--[\w-]+)\s*:/);
          if (pm) props.add(pm[1]);
        }
      }
      buf = "";
    } else if (ch === ";" && stack.at(-1) !== "rule" && stack.at(-1) !== "kfsel") buf = "";
    else buf += ch;
  }
  st.custom_properties_defined = props.size;
  st.infinite_animations = (s.match(/animation[^;]*\binfinite\b/g) ?? []).length;
  st.media_queries_distinct = new Set((s.match(/@media[^{]+/g) ?? []).map((m) => m.trim())).size;
  return st;
}
const css = {};
for (const f of files.filter((x) => x.endsWith(".css"))) css[rel(f)] = { bytes: Buffer.byteLength(text[f]), lines: text[f].split("\n").length, ...cssStats(text[f]) };

// ---------- tools ----------
function run(cmd, opts = {}) {
  const t0 = Date.now();
  try {
    const out = execSync(cmd, { cwd: FRONTEND, stdio: ["ignore", "pipe", "pipe"], maxBuffer: 64 << 20, env: { ...process.env, NO_COLOR: "1", FORCE_COLOR: "0" }, ...opts });
    return { ok: true, out: out.toString(), wall_s: (Date.now() - t0) / 1000 };
  } catch (e) {
    return { ok: false, out: `${e.stdout ?? ""}${e.stderr ?? ""}`, wall_s: (Date.now() - t0) / 1000 };
  }
}
let eslint = null,
  prettier = null,
  tsc = null,
  vitest = null;
if (!args.skip_lint) {
  const r = run("npx eslint . -f json");
  try {
    const j = JSON.parse(r.out.slice(r.out.indexOf("[")));
    const byRule = {};
    for (const f of j) for (const m of f.messages) byRule[m.ruleId ?? "fatal"] = (byRule[m.ruleId ?? "fatal"] ?? 0) + 1;
    eslint = {
      errors: j.reduce((s, f) => s + f.errorCount, 0),
      warnings: j.reduce((s, f) => s + f.warningCount, 0),
      files_linted: j.length,
      by_rule: byRule,
      wall_s: round(r.wall_s, 2),
    };
  } catch {
    eslint = { error: r.out.slice(0, 500) };
  }
  const p = run('npx prettier --check "src/**/*.{ts,tsx,css}" "e2e/**/*.ts"');
  const unformatted = (p.out.match(/^\[warn\] (?!Code style)[^\n]+/gm) ?? []).map((l) => l.replace("[warn] ", ""));
  prettier = { unformatted_files: unformatted.length, files: unformatted.slice(0, 20) };
  const t = run("npx tsc -b --noEmit");
  tsc = { errors: (t.out.match(/error TS\d+/g) ?? []).length, ok: t.ok, wall_s: round(t.wall_s, 2) };
}
if (!args.skip_tests) {
  const outFile = path.join(TMP, "fe_vitest.json");
  fs.mkdirSync(TMP, { recursive: true });
  const r = run(`npx vitest run --reporter=json --outputFile="${outFile}"`);
  try {
    const j = JSON.parse(fs.readFileSync(outFile, "utf8"));
    const perFile = j.testResults
      .map((t) => ({ file: path.relative(FRONTEND, t.name), tests: t.assertionResults.length, ms: Math.round(t.endTime - t.startTime) }))
      .sort((a, b) => b.ms - a.ms);
    vitest = {
      ok: r.ok,
      test_files: perFile.length,
      suites: j.numTotalTestSuites,
      tests_total: j.numTotalTests,
      tests_passed: j.numPassedTests,
      tests_failed: j.numFailedTests,
      wall_s: round(r.wall_s, 2),
      sum_file_ms: perFile.reduce((s, x) => s + x.ms, 0),
      slowest_files: perFile.slice(0, 8),
    };
  } catch {
    vitest = { ok: r.ok, wall_s: round(r.wall_s, 2), error: r.out.slice(-800) };
  }
}

const e2eDir = path.join(FRONTEND, "e2e");
const e2eFiles = fs.existsSync(e2eDir) ? fs.readdirSync(e2eDir).filter((f) => f.endsWith(".ts")) : [];

writeJson(path.join(rawDir(args.label), "fe_code.json"), {
  label: args.label,
  generated_at: new Date().toISOString(),
  git: gitInfo(),
  loc: { totals, top20_files: top20, e2e_files: e2eFiles.length },
  components: { count: components.length, over_150_lines: components.filter((c) => c.lines > 150).length, largest: components.slice(0, 15) },
  hooks,
  react_query: {
    client_defaults: qcDefaults,
    use_query_calls: queries.length,
    polling_queries: polling.length,
    queries,
  },
  casts: { app: castStats(appFiles.filter((f) => !f.endsWith(".css"))), tests: castStats(files.filter((f) => kind(f) === "test")) },
  inline_styles: { total: inlineTotal, by_file: inlineStyles },
  index_keys: indexKeys,
  date_now_in_tsx: nowInRender,
  duplicates: {
    names_defined_in_multiple_files: dupNames,
    format_helpers_outside_utils: fmtOutside,
    duplicated_6_line_windows: dupWindows.length,
    window_examples: dupExamples.slice(0, 12),
  },
  css,
  css_total: {
    bytes: Object.values(css).reduce((s, c) => s + c.bytes, 0),
    style_rules: Object.values(css).reduce((s, c) => s + c.style_rules, 0),
    media_queries: Object.values(css).reduce((s, c) => s + c.media_queries, 0),
    important: Object.values(css).reduce((s, c) => s + c.important, 0),
    infinite_animations: Object.values(css).reduce((s, c) => s + c.infinite_animations, 0),
  },
  eslint,
  prettier,
  tsc,
  vitest,
});
