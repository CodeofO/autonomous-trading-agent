#!/usr/bin/env node
// Merge docs/perf/raw/<label>/fe_*.json into docs/perf/<name>-frontend.json + .md
// (label "before" -> baseline-frontend.*, anything else -> <label>-frontend.*).
// The Markdown keeps everything between <!-- MANUAL:BEGIN --> and <!-- MANUAL:END --> across rebuilds
// (hand-written hotspots / observations live there).
//
//   node perf/fe_build_report.mjs [--label before]

import fs from "node:fs";
import path from "node:path";
import { ROOT, parseArgs, rawDir, readJson, round, writeJson } from "./fe_lib.mjs";

const args = parseArgs(process.argv.slice(2), { label: "before" });
const name = args.label === "before" ? "baseline" : args.label;
const OUT_JSON = path.join(ROOT, "docs", "perf", `${name}-frontend.json`);
const OUT_MD = path.join(ROOT, "docs", "perf", `${name}-frontend.md`);
const raw = (f) => readJson(path.join(rawDir(args.label), f));

const bundle = raw("fe_bundle.json");
const load = raw("fe_pageload.json");
const steady = raw("fe_steady.json");
const inter = raw("fe_interaction.json");
const render = raw("fe_render.json");
const code = raw("fe_code.json");

const g = (o, ...ks) => ks.reduce((v, k) => (v == null ? null : v[k]), o);
const routeMed = (prof, route, k) => g(load, "profiles", prof, "routes", route, "median", k);

// ---------- headline (flat, stable keys: compare BEFORE vs AFTER on these first) ----------
const headline = {
  bundle_js_raw: g(bundle, "totals", "js_raw"),
  bundle_js_gzip: g(bundle, "totals", "js_gzip"),
  bundle_initial_js_gzip: g(bundle, "totals", "initial_js_gzip"),
  bundle_css_raw: g(bundle, "totals", "css_raw"),
  bundle_js_chunks: g(bundle, "totals", "js_chunks"),
  bundle_routes_lazy: g(bundle, "code_split", "routes_lazy"),
  served_js_compressed: !!g(bundle, "served", "assets")?.some((a) => a.url.endsWith(".js") && a.content_encoding),
  served_js_bytes_on_wire: g(bundle, "served", "assets")?.find((a) => a.url.endsWith(".js"))?.bytes_on_wire ?? null,
};
for (const prof of ["desktop", "mobile_cpu4x", "mobile_cpu4x_fast3g"]) {
  const s = g(load, "summary", prof);
  if (!s) continue;
  headline[`load_${prof}_content_ms_median_of_routes`] = s.content_ms_median_of_routes;
  headline[`load_${prof}_content_ms_max_route`] = s.content_ms_max_route;
  headline[`load_${prof}_lcp_ms_median_of_routes`] = s.lcp_ms_median_of_routes;
  headline[`load_${prof}_long_task_ms_median_of_routes`] = s.long_task_ms_median_of_routes;
  headline[`load_${prof}_cls_max_route`] = s.cls_max_route;
}
headline.load_api_requests_median_of_routes = g(load, "summary", "desktop", "api_requests_median_of_routes");
headline.load_bytes_total_median_of_routes = g(load, "summary", "desktop", "bytes_total_median_of_routes");
for (const [k, p] of Object.entries(g(steady, "pages") ?? {})) {
  headline[`steady_${k}_api_per_min`] = p.api_requests_per_min;
  headline[`steady_${k}_react_commits_per_min`] = p.react_commits_per_min;
  headline[`steady_${k}_script_ms_per_min`] = p.script_ms_per_min;
  headline[`steady_${k}_task_ms_per_min`] = p.task_ms_per_min;
  headline[`steady_${k}_canvas_added_per_min`] = p.canvas_added_per_min;
}
for (const [k, p] of Object.entries(g(render, "pages") ?? {})) {
  headline[`render_${k}_component_renders_per_min`] = p.steady_component_renders_per_min;
  headline[`render_${k}_load_component_renders`] = p.load_component_renders_total;
}
for (const prof of ["desktop", "mobile_cpu4x"]) {
  for (const sc of ["room_scroll", "dashboard_chat_scroll"]) {
    headline[`${sc}_${prof}_frames_over_50ms`] = g(inter, sc, prof, "median", "frames_over_50ms");
    headline[`${sc}_${prof}_frame_p95_ms`] = g(inter, sc, prof, "median", "frame_p95_ms");
  }
  for (const st of ["cold_kr_to_us", "cold_us_to_all", "warm_kr_to_us"]) {
    headline[`switch_${prof}_${st}_content_ms`] = g(inter, "market_switch", prof, st, "content_ms");
  }
}
Object.assign(headline, {
  code_app_lines: g(code, "loc", "totals", "app", "lines"),
  code_css_lines: g(code, "loc", "totals", "css", "lines"),
  code_components_over_150_lines: g(code, "components", "over_150_lines"),
  code_use_query_calls: g(code, "react_query", "use_query_calls"),
  code_polling_queries: g(code, "react_query", "polling_queries"),
  code_react_memo: g(code, "hooks", "react_memo"),
  code_inline_styles: g(code, "inline_styles", "total"),
  code_as_casts_app: g(code, "casts", "app", "as_casts"),
  code_any_app: g(code, "casts", "app", "any"),
  code_dup_windows: g(code, "duplicates", "duplicated_6_line_windows"),
  code_css_style_rules: g(code, "css_total", "style_rules"),
  code_eslint_errors: g(code, "eslint", "errors"),
  code_eslint_warnings: g(code, "eslint", "warnings"),
  code_tsc_errors: g(code, "tsc", "errors"),
  code_tests_total: g(code, "vitest", "tests_total"),
  code_tests_failed: g(code, "vitest", "tests_failed"),
  code_vitest_wall_s: g(code, "vitest", "wall_s"),
});

const merged = {
  label: args.label,
  generated_at: new Date().toISOString(),
  git: g(code, "git") ?? g(bundle, "git"),
  base_url: g(load, "base_url"),
  chromium: g(load, "chromium"),
  server_state: { load_before: g(load, "server_state_before"), load_after: g(load, "server_state_after") },
  headline,
  bundle,
  pageload: load,
  steady,
  interaction: inter,
  render,
  code,
};
writeJson(OUT_JSON, merged);

// ---------- Markdown ----------
const n = (v, d = 0) => (v == null ? "—" : typeof v === "number" ? v.toLocaleString("en-US", { maximumFractionDigits: d }) : String(v));
const kb = (b) => (b == null ? "—" : `${(b / 1024).toFixed(1)} KB`);
const table = (head, rows) => [`| ${head.join(" | ")} |`, `|${head.map(() => "---").join("|")}|`, ...rows.map((r) => `| ${r.join(" | ")} |`)].join("\n");
const L = [];
L.push(`# Frontend performance baseline (${args.label === "before" ? "BEFORE" : args.label.toUpperCase()})`);
L.push("");
L.push(
  `Generated ${merged.generated_at} from git \`${merged.git?.head}\` on \`${merged.git?.branch}\` (${merged.git?.frontend_uncommitted_paths} uncommitted paths under frontend/). ` +
    `Live app ${merged.base_url} (nginx, production build) against the running paper-trading backend; headless Chromium ${merged.chromium}. ` +
    "Re-run with the commands in `perf/README.md` (Frontend section); compare runs with `node perf/fe_compare.mjs`.",
);
L.push("");
L.push(`Market state while loading pages: \`${JSON.stringify(merged.server_state.load_before)}\`. Times below are medians of ${g(load, "runs_per_route") ?? "?"} cold runs (Fast 3G: 1 run).`);
L.push("");

L.push("## Headline numbers");
L.push("");
const hp = (prof) => g(load, "summary", prof) ?? {};
L.push(
  `- **Bundle**: one JS chunk ${kb(headline.bundle_js_raw)} raw / ${kb(headline.bundle_js_gzip)} gzip, CSS ${kb(headline.bundle_css_raw)}; route code-splitting: **${headline.bundle_routes_lazy ? "yes" : "no"}**. ` +
    `Served by nginx **${headline.served_js_compressed ? "compressed" : "uncompressed"}** (${kb(headline.served_js_bytes_on_wire)} on the wire), no Cache-Control.`,
);
L.push(
  `- **Page load (content visible, median of 13 routes)**: desktop ${n(hp("desktop").content_ms_median_of_routes)} ms, mobile 4x CPU ${n(hp("mobile_cpu4x").content_ms_median_of_routes)} ms, mobile 4x CPU + Fast 3G ${n(hp("mobile_cpu4x_fast3g").content_ms_median_of_routes)} ms ` +
    `(LCP ${n(hp("mobile_cpu4x_fast3g").lcp_ms_median_of_routes)} ms). Every route makes ~${n(headline.load_api_requests_median_of_routes)} API requests and transfers ~${kb(headline.load_bytes_total_median_of_routes)} cold.`,
);
for (const [k, p] of Object.entries(g(steady, "pages") ?? {})) {
  L.push(
    `- **Idle on ${k}** (desktop, ${steady.seconds} s): ${n(p.api_requests_per_min, 1)} API req/min, ${n(p.react_commits_per_min)} React commits/min, ${n(p.script_ms_per_min)} ms script + ${n(p.task_ms_per_min)} ms main-thread tasks per minute, ${n(p.recalc_style_count_per_min)} style recalcs/min, ${n(p.canvas_added_per_min)} chart canvases re-created/min.`,
  );
}
for (const [k, p] of Object.entries(g(render, "pages") ?? {})) {
  L.push(`- **Component renders while idle on ${k}**: ${n(p.steady_component_renders_per_min)} per minute (top: ${Object.entries(p.steady_rerenders_per_min_top ?? {}).slice(0, 4).map(([c, v]) => `${c} ${n(v)}`).join(", ")}).`);
}
const ms = (prof, st) => g(inter, "market_switch", prof, st, "content_ms");
L.push(
  `- **Market switch** (click -> new content): desktop 국장->미장 ${n(ms("desktop", "cold_kr_to_us"))} ms, 미장->전체 ${n(ms("desktop", "cold_us_to_all"))} ms; mobile 4x ${n(ms("mobile_cpu4x", "cold_kr_to_us"))} / ${n(ms("mobile_cpu4x", "cold_us_to_all"))} ms.`,
);
const sc = (s, prof, k) => g(inter, s, prof, "median", k);
L.push(
  `- **Chat scroll** (top and back, ${g(inter, "scroll_step_px")} px/frame): /room mobile 4x ${n(sc("room_scroll", "mobile_cpu4x", "frames_over_50ms"))} frames > 50 ms (p95 ${n(sc("room_scroll", "mobile_cpu4x", "frame_p95_ms"), 1)} ms); dashboard chat mobile 4x ${n(sc("dashboard_chat_scroll", "mobile_cpu4x", "frames_over_50ms"))} frames > 50 ms (p95 ${n(sc("dashboard_chat_scroll", "mobile_cpu4x", "frame_p95_ms"), 1)} ms).`,
);
L.push(
  `- **Code**: ${n(headline.code_app_lines)} app lines + ${n(headline.code_css_lines)} CSS lines; ${n(headline.code_use_query_calls)} useQuery (${n(headline.code_polling_queries)} polling), ${n(headline.code_react_memo)} React.memo; eslint ${n(headline.code_eslint_errors)} errors / ${n(headline.code_eslint_warnings)} warnings; ${n(headline.code_tests_total)} tests (${n(headline.code_tests_failed)} failed) in ${n(headline.code_vitest_wall_s, 1)} s.`,
);
L.push("");

// bundle
L.push("## 1. Bundle");
L.push("");
if (bundle) {
  L.push(`\`${bundle.build.command}\` (${bundle.build.wall_s} s). gzip level 9, brotli q11.`);
  L.push("");
  L.push(table(["chunk", "type", "raw", "gzip", "brotli", "entry", "dynamic", "initial"], bundle.chunks.map((c) => [c.file, c.type, n(c.raw), n(c.gzip), n(c.brotli), c.is_entry ? "y" : "", c.is_dynamic_entry ? "y" : "", c.initial ? "y" : ""])));
  L.push("");
  L.push(`Code splitting: React.lazy calls ${bundle.code_split.react_lazy_calls}, dynamic import() ${bundle.code_split.dynamic_imports_in_src}, dynamic chunks ${bundle.code_split.dynamic_entry_chunks}, pages imported statically in App.tsx ${bundle.code_split.pages_imported_statically_in_app_tsx}.`);
  L.push("");
  L.push("**Bytes by package (minified, attributed through sourcemaps)**");
  L.push("");
  L.push(table(["package", "bytes", "%"], bundle.by_package.slice(0, 12).map((p) => [p.name, n(p.bytes), n(p.pct, 1)])));
  L.push("");
  L.push("**Largest app source files in the bundle**");
  L.push("");
  L.push(table(["file", "bytes", "% of bundle"], bundle.app_src_top_files.slice(0, 10).map((p) => [p.name, n(p.bytes), n(p.pct, 1)])));
  L.push("");
  L.push("**What the live server sends**");
  L.push("");
  L.push(table(["asset", "Content-Encoding", "Cache-Control", "bytes on wire"], (bundle.served.assets ?? []).map((a) => [a.url, a.content_encoding ?? "none", a.cache_control ?? "none", n(a.bytes_on_wire)])));
  L.push("");
}

// page load
L.push("## 2. Page load (cold, fresh browser context per run)");
L.push("");
L.push("`content` = first animation frame in which the route's data-dependent selector is present (see `ROUTES` in perf/fe_lib.mjs). `idle` = no /api request in flight for 1.5 s. Long tasks / CDP metrics cover navigation -> idle. Bytes = encoded bytes over the wire incl. headers.");
L.push("");
for (const [prof, p] of Object.entries(g(load, "profiles") ?? {})) {
  L.push(`### ${prof} (CPU x${p.cpu_throttle}${p.network ? ", Fast 3G" : ""})`);
  L.push("");
  L.push(
    table(
      ["route", "path", "FCP", "LCP", "content", "DCL", "load", "idle", "CLS", "long tasks (ms)", "API req", "KB", "heap MB", "DOM nodes", "script ms"],
      Object.entries(p.routes).map(([k, r]) => {
        const m = r.median;
        return [k, `\`${r.path}\``, n(m.fcp_ms), n(m.lcp_ms), n(m.content_ms) + (r.content_timeouts ? " (timeout)" : ""), n(m.dcl_ms), n(m.load_ms), n(m.idle_ms), n(m.cls, 3), `${n(m.long_tasks)} (${n(m.long_task_ms)})`, n(m.api_requests), n(m.bytes_total / 1024), n(m.js_heap_used_mb, 1), n(m.dom_nodes), n(m.script_ms)];
      }),
    ),
  );
  L.push("");
}
const ep = g(load, "profiles", "desktop", "routes", "room_kr", "api_endpoints_run1");
if (ep) {
  L.push(`API requests made by a cold load of /room (desktop, run 1): \`${JSON.stringify(ep)}\``);
  L.push("");
}
const blocked = Object.entries(g(load, "profiles", "desktop", "routes") ?? {}).filter(([, r]) => r.blocked_writes?.length);
if (blocked.length) {
  L.push(`Writes attempted on page open and blocked by the guard: ${blocked.map(([k, r]) => `${k}: ${[...new Set(r.blocked_writes)].join(", ")}`).join("; ")}`);
  L.push("");
}

// steady
L.push("## 3. Steady state (desktop, page left open)");
L.push("");
if (steady) {
  const pages = Object.entries(steady.pages);
  const keys = [
    ["api_requests_per_min", "API req/min"],
    ["api_bytes_per_min", "API bytes/min"],
    ["ws_frames_per_min", "WS frames/min"],
    ["react_commits_per_min", "React commits/min"],
    ["canvas_added_per_min", "chart canvases created/min"],
    ["long_tasks_per_min", "long tasks/min"],
    ["long_task_ms_per_min", "long task ms/min"],
    ["script_ms_per_min", "script ms/min"],
    ["task_ms_per_min", "main-thread task ms/min"],
    ["layout_count_per_min", "layouts/min"],
    ["recalc_style_count_per_min", "style recalcs/min"],
    ["js_heap_used_mb_start", "heap MB start"],
    ["js_heap_used_mb_end", "heap MB end"],
    ["dom_nodes_end", "DOM nodes"],
  ];
  L.push(table(["metric", ...pages.map(([k]) => k)], keys.map(([k, label]) => [label, ...pages.map(([, p]) => n(p[k], 1))])));
  L.push("");
  for (const [k, p] of pages) L.push(`- ${k} API calls/min by endpoint: \`${JSON.stringify(p.api_by_endpoint_per_min)}\`; WS by channel/min: \`${JSON.stringify(p.ws_by_channel_per_min)}\`; market state \`${JSON.stringify(p.server_state_before)}\``);
  L.push("");
}

// render
if (render) {
  L.push("## 4. React render counts (unminified prod build via `vite preview`, DevTools-hook fiber walk)");
  L.push("");
  for (const [k, p] of Object.entries(render.pages)) {
    L.push(`- **${k}**: load = ${n(p.load_commits)} commits / ${n(p.load_component_renders_total)} component renders; idle = ${n(p.steady_commits_per_min)} commits/min, ${n(p.steady_component_renders_per_min)} component renders/min. Re-renders/min: \`${JSON.stringify(p.steady_rerenders_per_min_top)}\``);
  }
  L.push("");
}

// interaction
if (inter) {
  L.push("## 5. Interaction smoothness");
  L.push("");
  for (const s of ["room_scroll", "dashboard_chat_scroll"]) {
    L.push(`**${s}** (programmatic scroll ${inter.scroll_step_px} px per animation frame to the top and back)`);
    L.push("");
    L.push(
      table(
        ["profile", "scroller", "scroll px", "rows", "frames", "> 50 ms", "> 33 ms", "p50 ms", "p95 ms", "max ms", "avg fps", "commits", "long tasks"],
        Object.entries(inter[s]).map(([prof, r]) => [prof, r.scroller, n(r.median.scroll_height), n(r.median.chat_rows), n(r.median.frames), n(r.median.frames_over_50ms), n(r.median.frames_over_33ms), n(r.median.frame_p50_ms, 1), n(r.median.frame_p95_ms, 1), n(r.median.frame_max_ms, 1), n(r.median.avg_fps, 1), n(r.median.react_commits), n(r.median.long_tasks)]),
      ),
    );
    L.push("");
  }
  L.push("**MarketSwitch** (start on /?m=kr; click -> aria-pressed (ui) / new content visible / view transition finished; click event duration from Event Timing)");
  L.push("");
  const rows = [];
  for (const [prof, steps] of Object.entries(inter.market_switch)) {
    for (const [st, v] of Object.entries(steps)) {
      if (st === "all_runs") continue;
      rows.push([prof, st, n(v.ui_ms, 1), n(v.content_ms, 1), n(v.view_transition_ms, 1), n(v.click_event_duration_ms, 1), n(v.commits), n(v.api_requests)]);
    }
  }
  L.push(table(["profile", "step", "ui ms", "content ms", "transition done ms", "click event ms", "commits", "API req"], rows));
  L.push("");
}

// code
if (code) {
  L.push("## 6. Code metrics");
  L.push("");
  const t = code.loc.totals;
  L.push(`app ${t.app.files} files / ${n(t.app.lines)} lines, tests ${t.test.files} files / ${n(t.test.lines)} lines, CSS ${t.css.files} files / ${n(t.css.lines)} lines, e2e specs ${code.loc.e2e_files}.`);
  L.push("");
  L.push(table(["file", "kind", "lines"], code.loc.top20_files.map((f) => [f.file, f.kind, n(f.lines)])));
  L.push("");
  L.push(`**Largest components** (${code.components.count} components, ${code.components.over_150_lines} over 150 lines)`);
  L.push("");
  L.push(table(["component", "at", "lines"], code.components.largest.slice(0, 12).map((c) => [c.name, `${c.file}:${c.line}`, n(c.lines)])));
  L.push("");
  L.push(`**Hooks**: \`${JSON.stringify(code.hooks)}\``);
  L.push("");
  L.push(`**React Query**: QueryClient defaults \`${code.react_query.client_defaults}\`; ${code.react_query.use_query_calls} useQuery calls, ${code.react_query.polling_queries} with refetchInterval:`);
  L.push("");
  L.push(table(["at", "queryKey", "endpoint", "refetchInterval", "staleTime"], code.react_query.queries.filter((q) => q.refetchInterval || q.staleTime).map((q) => [q.at, `\`${q.queryKey}\``, q.endpoint ?? "", q.refetchInterval ?? "", q.staleTime ?? "(default 2000)"])));
  L.push("");
  L.push(`**Type escapes (app code)**: \`${JSON.stringify(code.casts.app)}\`; tests: \`${JSON.stringify(code.casts.tests)}\``);
  L.push("");
  L.push(`**Inline style props**: ${code.inline_styles.total} (\`${JSON.stringify(code.inline_styles.by_file)}\`); index keys: ${code.index_keys}.`);
  L.push("");
  L.push(`**Duplicates**: ${code.duplicates.duplicated_6_line_windows} duplicated 6-line windows; same name defined in several files: ${code.duplicates.names_defined_in_multiple_files.map((d) => `\`${d.name}\` (${d.at.join(", ")})`).join("; ")}. Format helpers outside src/utils: ${code.duplicates.format_helpers_outside_utils.map((d) => `\`${d.name}\` ${d.at}`).join(", ")}.`);
  L.push("");
  L.push("**CSS**");
  L.push("");
  L.push(table(["file", "bytes", "lines", "rules", "selectors", "declarations", "@media", "@keyframes", "infinite anims", "!important", "custom props"], Object.entries(code.css).map(([f, c]) => [f, n(c.bytes), n(c.lines), n(c.style_rules), n(c.selectors), n(c.declarations), n(c.media_queries), n(c.keyframes), n(c.infinite_animations), n(c.important), n(c.custom_properties_defined)])));
  L.push("");
  if (code.eslint) L.push(`**eslint**: ${code.eslint.errors} errors, ${code.eslint.warnings} warnings over ${code.eslint.files_linted} files (${code.eslint.wall_s} s) \`${JSON.stringify(code.eslint.by_rule)}\`; prettier unformatted files: ${code.prettier?.unformatted_files}; tsc errors: ${code.tsc?.errors} (${code.tsc?.wall_s} s).`);
  L.push("");
  if (code.vitest) L.push(`**vitest**: ${code.vitest.test_files} files, ${code.vitest.tests_total} tests, ${code.vitest.tests_failed} failed, wall ${code.vitest.wall_s} s. Slowest files: ${(code.vitest.slowest_files ?? []).slice(0, 5).map((f) => `${f.file} ${f.ms} ms`).join(", ")}.`);
  L.push("");
}

// manual block
const BEGIN = "<!-- MANUAL:BEGIN -->",
  END = "<!-- MANUAL:END -->";
let manual = `${BEGIN}\n## 7. Hotspots and observations (hand-written, kept across rebuilds)\n\n_(fill in)_\n${END}`;
if (fs.existsSync(OUT_MD)) {
  const old = fs.readFileSync(OUT_MD, "utf8");
  const i = old.indexOf(BEGIN),
    j = old.indexOf(END);
  if (i >= 0 && j > i) manual = old.slice(i, j + END.length);
}
L.push(manual);
L.push("");
fs.writeFileSync(OUT_MD, L.join("\n"));
console.log(`wrote ${path.relative(ROOT, OUT_MD)}`);
void round;
