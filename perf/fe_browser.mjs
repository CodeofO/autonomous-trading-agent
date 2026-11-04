#!/usr/bin/env node
// Frontend runtime baseline with Playwright chromium (headless). Pages are only loaded and navigated:
// the only click this script makes is the MarketSwitch (전체/국장/미장), a pure client-side view switch.
// Every page runs with GUARD_SCRIPT (fe_lib.mjs): fetch/XHR/sendBeacon refuse non-GET requests, and the
// CDP listener records any non-GET /api request that still reaches the network (guard_violations).
//
//   node perf/fe_browser.mjs --suite load|steady|interact|render|all [--label before]
//        [--base-url http://localhost:15173] [--runs 3] [--seconds 60] [--render-seconds 60]
//        [--routes overview,room_kr] [--profiles desktop,mobile_cpu4x,mobile_cpu4x_fast3g]
//        [--backend-url http://127.0.0.1:18000] [--render-port 15183]
//   -> docs/perf/raw/<label>/fe_pageload.json | fe_steady.json | fe_interaction.json | fe_render.json
//
// load     : every route x profile, N runs (fast3g: 1 run), cold context each time; medians
// steady   : overview / dashboard_kr / room_kr on desktop, N seconds each: API calls per endpoint, WS frames,
//            long tasks, React commits, CPU time (CDP Performance metrics), heap/DOM growth, chart canvases created
// interact : /room chat scroll to top and back (rAF frame deltas), MarketSwitch 국장->미장->전체 (cold + warm)
// render   : component render counts (React DevTools-hook fiber walk) on an UNMINIFIED production build served
//            by `vite preview` (proxying /api and /ws to --backend-url); no source changes needed

import { spawn, execSync } from "node:child_process";
import path from "node:path";
import {
  FRONTEND,
  GUARD_SCRIPT,
  PROFILES,
  ROUTE,
  ROUTES,
  TMP,
  attachCdp,
  clsOf,
  endpointKey,
  gitInfo,
  loadPlaywright,
  longTaskStats,
  median,
  medianRecord,
  metricsScript,
  parseArgs,
  pct,
  rawDir,
  round,
  serverState,
  sleep,
  writeJson,
} from "./fe_lib.mjs";

const args = parseArgs(process.argv.slice(2), {
  suite: "all",
  label: "before",
  base_url: "http://localhost:15173",
  runs: "3",
  seconds: "60",
  render_seconds: "60",
  routes: "",
  profiles: "desktop,mobile_cpu4x,mobile_cpu4x_fast3g",
  backend_url: "http://127.0.0.1:18000",
  render_port: "15183",
});
const RUNS = Number(args.runs);
const BASE = args.base_url.replace(/\/$/, "");
const { chromium } = loadPlaywright();
const suites = args.suite === "all" ? ["load", "steady", "interact", "render"] : args.suite.split(",");
let BROWSER_VERSION = null;
const pickRoutes = args.routes ? args.routes.split(",").map((k) => ROUTE[k]) : ROUTES;

const KPI_KR = `(() => { const v = document.querySelector('.dashboard .kpis .kpi-value'); const t = v && v.textContent.trim(); return !!t && t !== '—' && !t.includes('$'); })()`;
const KPI_US = `(() => { const v = document.querySelector('.dashboard .kpis .kpi-value'); const t = v && v.textContent.trim(); return !!t && t.includes('$'); })()`;
const OV = `!!document.querySelector('[data-testid="ov-total"]')`;

function meta() {
  return {
    label: args.label,
    generated_at: new Date().toISOString(),
    git: gitInfo(),
    base_url: BASE,
    node: process.version,
    chromium: BROWSER_VERSION,
  };
}

async function newPage(browser, profileName, { ready = "false", reactHook = "off", baseUrl = BASE } = {}) {
  const prof = PROFILES[profileName];
  const context = await browser.newContext({
    ...prof.context,
    baseURL: baseUrl,
    locale: "ko-KR",
    timezoneId: "Asia/Seoul",
    colorScheme: "light",
    reducedMotion: "no-preference",
  });
  await context.addInitScript(GUARD_SCRIPT);
  await context.addInitScript(metricsScript({ ready, reactHook }));
  const page = await context.newPage();
  const { cdp, net } = await attachCdp(context, page, prof);
  return { context, page, cdp, net };
}

async function waitContent(page, timeout) {
  return page
    .waitForFunction(() => window.__perf && window.__perf.contentAt != null, null, { timeout, polling: 100 })
    .then(() => true)
    .catch(() => false);
}

const blockedIn = (page) => page.evaluate(() => window.__perfBlocked ?? []).catch(() => []);

// ---------------------------------------------------------------- load
async function loadOnce(browser, route, profileName) {
  const fast = !!PROFILES[profileName].network;
  const { context, page, net } = await newPage(browser, profileName, { ready: route.ready });
  try {
    const t0 = Date.now();
    await page.goto(route.path, { waitUntil: "commit", timeout: fast ? 120_000 : 60_000 });
    const ok = await waitContent(page, fast ? 90_000 : 30_000);
    await page.waitForLoadState("load", { timeout: fast ? 60_000 : 20_000 }).catch(() => {});
    await net.waitApiIdle(1500, fast ? 30_000 : 15_000);
    const idleAt = Date.now();
    const reqs = [...net.reqs.values()].filter((r) => r.start <= idleAt);
    const p = await page.evaluate(() => {
      const P = window.__perf;
      const nav = performance.getEntriesByType("navigation")[0];
      const fcp = performance.getEntriesByName("first-contentful-paint")[0];
      return {
        dcl: nav ? nav.domContentLoadedEventEnd : null,
        load: nav ? nav.loadEventEnd || null : null,
        ttfb: nav ? nav.responseStart : null,
        fcp: fcp ? fcp.startTime : null,
        lcp: P.lcp,
        lcpTag: P.lcpTag,
        contentAt: P.contentAt,
        shifts: P.shifts,
        longtasks: P.longtasks,
        canvasAdded: P.canvasAdded,
        now: performance.now(),
      };
    });
    await net.gc();
    const m = await net.perfMetrics();
    const lt = longTaskStats(p.longtasks);
    const api = reqs.filter((r) => r.api);
    return {
      ok,
      dcl_ms: round(p.dcl),
      load_ms: round(p.load),
      ttfb_ms: round(p.ttfb),
      fcp_ms: round(p.fcp),
      lcp_ms: round(p.lcp),
      content_ms: round(p.contentAt),
      idle_ms: round(p.now),
      cls: round(clsOf(p.shifts), 4),
      ...lt,
      api_requests: api.length,
      api_failed: api.filter((r) => r.failed).length,
      requests_total: reqs.length,
      bytes_total: reqs.reduce((s, r) => s + r.bytes, 0),
      bytes_api: api.reduce((s, r) => s + r.bytes, 0),
      bytes_static: reqs.filter((r) => !r.api).reduce((s, r) => s + r.bytes, 0),
      js_heap_used_mb: round(m.JSHeapUsedSize / 1048576, 2),
      dom_nodes: m.Nodes,
      js_event_listeners: m.JSEventListeners,
      layout_count: m.LayoutCount,
      recalc_style_count: m.RecalcStyleCount,
      script_ms: round(m.ScriptDuration * 1000),
      layout_ms: round(m.LayoutDuration * 1000),
      recalc_style_ms: round(m.RecalcStyleDuration * 1000),
      task_ms: round(m.TaskDuration * 1000),
      canvas_added: p.canvasAdded,
      lcp_element: p.lcpTag,
      wall_ms: Date.now() - t0,
      api_endpoints: Object.fromEntries(
        Object.entries(
          api.reduce((o, r) => ((o[endpointKey(r.url)] = (o[endpointKey(r.url)] ?? 0) + 1), o), {}),
        ).sort(),
      ),
      blocked_writes: await blockedIn(page),
      guard_violations: net.violations,
    };
  } finally {
    await context.close();
  }
}

async function suiteLoad(browser) {
  const profiles = args.profiles.split(",");
  const out = { ...meta(), runs_per_route: RUNS, server_state_before: await serverState(BASE), profiles: {} };
  const raw = {};
  for (const prof of profiles) {
    const runs = PROFILES[prof].network ? 1 : RUNS;
    raw[prof] = Object.fromEntries(pickRoutes.map((r) => [r.key, []]));
    for (let i = 0; i < runs; i++) {
      for (const route of pickRoutes) {
        const r = await loadOnce(browser, route, prof);
        raw[prof][route.key].push(r);
        console.log(
          `[load] ${prof} ${route.key} #${i + 1}: content ${r.content_ms} ms, lcp ${r.lcp_ms}, api ${r.api_requests}, ${Math.round(r.bytes_total / 1024)} KB${r.ok ? "" : " (CONTENT TIMEOUT)"}`,
        );
        if (r.guard_violations.length) throw new Error(`guard violation: ${JSON.stringify(r.guard_violations)}`);
      }
    }
  }
  for (const [prof, routes] of Object.entries(raw)) {
    out.profiles[prof] = { cpu_throttle: PROFILES[prof].cpu, network: PROFILES[prof].network, routes: {} };
    for (const [key, runs] of Object.entries(routes)) {
      const numeric = runs.map(({ api_endpoints, blocked_writes, guard_violations, lcp_element, ok, ...n }) => n);
      out.profiles[prof].routes[key] = {
        path: ROUTE[key].path,
        runs: runs.length,
        content_timeouts: runs.filter((r) => !r.ok).length,
        median: medianRecord(numeric),
        lcp_element: runs[0]?.lcp_element ?? null,
        api_endpoints_run1: runs[0]?.api_endpoints ?? {},
        blocked_writes: runs.flatMap((r) => r.blocked_writes).map((b) => `${b.method} ${new URL(b.url, BASE).pathname}`),
        all_runs: numeric,
      };
    }
  }
  out.server_state_after = await serverState(BASE);
  // headline: medians across routes per profile
  out.summary = {};
  for (const [prof, p] of Object.entries(out.profiles)) {
    const rs = Object.values(p.routes).map((r) => r.median);
    const col = (k) => rs.map((r) => r[k]);
    out.summary[prof] = {
      content_ms_median_of_routes: round(median(col("content_ms"))),
      content_ms_max_route: round(Math.max(...col("content_ms").filter((x) => x != null))),
      lcp_ms_median_of_routes: round(median(col("lcp_ms"))),
      fcp_ms_median_of_routes: round(median(col("fcp_ms"))),
      long_task_ms_median_of_routes: round(median(col("long_task_ms"))),
      api_requests_median_of_routes: round(median(col("api_requests"))),
      bytes_total_median_of_routes: round(median(col("bytes_total")), 0),
      cls_max_route: round(Math.max(...col("cls").filter((x) => x != null)), 4),
    };
  }
  writeJson(path.join(rawDir(args.label), "fe_pageload.json"), out);
}

// ---------------------------------------------------------------- steady
async function steadyOnce(browser, route, seconds, { reactHook = "count", baseUrl = BASE } = {}) {
  const { context, page, net } = await newPage(browser, "desktop", { ready: route.ready, reactHook, baseUrl });
  try {
    await page.goto(route.path, { waitUntil: "commit" });
    const ok = await waitContent(page, 30_000);
    await net.waitApiIdle(1500, 15_000);
    const before = await serverState(BASE);
    await net.gc();
    const m0 = await net.perfMetrics();
    const s0 = await page.evaluate(() => {
      const P = window.__perf;
      // no rAF frame loop here on purpose: a continuous rAF makes Chrome service CSS animations on the
      // main thread every frame and inflates the idle CPU numbers (jank is captured by long tasks instead)
      P.renders = {};
      P.mounts = {};
      return { t: performance.now(), commits: P.commits, canvas: P.canvasAdded, vis: document.visibilityState };
    });
    const startWall = Date.now();
    net.resetWs();
    await sleep(seconds * 1000);
    const endWall = Date.now();
    const s1 = await page.evaluate(() => {
      const P = window.__perf;
      return {
        t: performance.now(),
        commits: P.commits,
        canvas: P.canvasAdded,
        longtasks: P.longtasks,
        renders: P.renders,
        mounts: P.mounts,
      };
    });
    await net.gc();
    const m1 = await net.perfMetrics();
    const reqs = [...net.reqs.values()].filter((r) => r.api && r.start >= startWall && r.start < endWall);
    const byEp = {};
    for (const r of reqs) byEp[endpointKey(r.url)] = (byEp[endpointKey(r.url)] ?? 0) + 1;
    const perMin = 60 / seconds;
    const d = (k, mul = 1) => round((m1[k] - m0[k]) * mul * perMin, 1);
    return {
      path: route.path,
      ok,
      seconds,
      document_visibility: s0.vis,
      server_state_before: before,
      server_state_after: await serverState(BASE),
      api_requests_per_min: round(reqs.length * perMin, 1),
      api_bytes_per_min: round(reqs.reduce((s, r) => s + r.bytes, 0) * perMin, 0),
      api_by_endpoint_per_min: Object.fromEntries(
        Object.entries(byEp)
          .sort((a, b) => b[1] - a[1])
          .map(([k, v]) => [k, round(v * perMin, 1)]),
      ),
      ws_frames_per_min: round(net.ws.frames * perMin, 1),
      ws_bytes_per_min: round(net.ws.bytes * perMin, 0),
      ws_by_channel_per_min: Object.fromEntries(
        Object.entries(net.ws.byChannel).map(([k, v]) => [k, round(v * perMin, 1)]),
      ),
      ...Object.fromEntries(
        Object.entries(longTaskStats(s1.longtasks, s0.t, s1.t)).map(([k, v]) => [`${k}_per_min`, round(v * perMin, 1)]),
      ),
      react_commits_per_min: round((s1.commits - s0.commits) * perMin, 1),
      canvas_added_per_min: round((s1.canvas - s0.canvas) * perMin, 1),
      script_ms_per_min: d("ScriptDuration", 1000),
      task_ms_per_min: d("TaskDuration", 1000),
      layout_count_per_min: d("LayoutCount"),
      recalc_style_count_per_min: d("RecalcStyleCount"),
      layout_ms_per_min: d("LayoutDuration", 1000),
      recalc_style_ms_per_min: d("RecalcStyleDuration", 1000),
      js_heap_used_mb_start: round(m0.JSHeapUsedSize / 1048576, 2),
      js_heap_used_mb_end: round(m1.JSHeapUsedSize / 1048576, 2),
      dom_nodes_start: m0.Nodes,
      dom_nodes_end: m1.Nodes,
      renders: s1.renders,
      mounts: s1.mounts,
      blocked_writes: await blockedIn(page),
      guard_violations: net.violations,
    };
  } finally {
    await context.close();
  }
}

async function suiteSteady(browser) {
  const seconds = Number(args.seconds);
  const out = { ...meta(), profile: "desktop", seconds, pages: {} };
  for (const key of ["overview", "dashboard_kr", "room_kr"]) {
    console.log(`[steady] ${key} for ${seconds} s ...`);
    const r = await steadyOnce(browser, ROUTE[key], seconds);
    delete r.renders;
    delete r.mounts;
    out.pages[key] = r;
    console.log(
      `[steady] ${key}: ${r.api_requests_per_min} API/min ${JSON.stringify(r.api_by_endpoint_per_min)}, WS ${r.ws_frames_per_min}/min, commits ${r.react_commits_per_min}/min, long tasks ${r.long_tasks_per_min}/min, script ${r.script_ms_per_min} ms/min`,
    );
    if (r.guard_violations.length) throw new Error("guard violation");
  }
  writeJson(path.join(rawDir(args.label), "fe_steady.json"), out);
}

// ---------------------------------------------------------------- interact
const SCROLL_STEP_PX = 40; // per animation frame (~2400 px/s, a brisk wheel/fling)

async function chatScrollOnce(browser, profileName, routeKey) {
  const { context, page, net } = await newPage(browser, profileName, { ready: ROUTE[routeKey].ready, reactHook: "count" });
  try {
    await page.goto(ROUTE[routeKey].path, { waitUntil: "commit" });
    const ok = await waitContent(page, 30_000);
    await net.waitApiIdle(1500, 15_000);
    await sleep(500);
    const res = await page.evaluate(async (step) => {
      const P = window.__perf;
      const chat = document.querySelector('[data-testid="chat-scroll"]');
      const pageScroll = document.querySelector('[data-testid="page-scroll"]');
      const el = chat && chat.scrollHeight > chat.clientHeight + 10 ? chat : pageScroll;
      const which = el === chat ? "chat-scroll" : "page-scroll";
      const frames = [];
      const c0 = P.commits;
      const t0 = performance.now();
      const startTop = el.scrollTop;
      await new Promise((resolve) => {
        let dir = -1,
          last = performance.now();
        const tick = (t) => {
          frames.push(t - last);
          last = t;
          const max = el.scrollHeight - el.clientHeight;
          if (dir < 0) {
            el.scrollTop = Math.max(0, el.scrollTop - step);
            if (el.scrollTop <= 0) dir = 1;
          } else {
            el.scrollTop = Math.min(max, el.scrollTop + step);
            if (el.scrollTop >= max - 1) return resolve();
          }
          if (performance.now() - t0 > 60000) return resolve();
          requestAnimationFrame(tick);
        };
        requestAnimationFrame((t) => {
          last = t;
          requestAnimationFrame(tick);
        });
      });
      const t1 = performance.now();
      const lt = P.longtasks.filter(([s]) => s >= t0 && s < t1);
      return {
        scroller: which,
        scroll_height: el.scrollHeight,
        client_height: el.clientHeight,
        start_top: startTop,
        step_px: step,
        duration_ms: t1 - t0,
        frames: frames.slice(1),
        commits: P.commits - c0,
        long_tasks: lt.length,
        long_task_ms: lt.reduce((s, [, d]) => s + d, 0),
        chat_rows: document.querySelectorAll('[data-testid="chat-scroll"] .chat-row, [data-testid="session"]').length,
        dom_nodes: document.getElementsByTagName("*").length,
      };
    }, SCROLL_STEP_PX);
    const f = res.frames;
    return {
      ok,
      scroller: res.scroller,
      scroll_height: res.scroll_height,
      step_px: res.step_px,
      chat_rows: res.chat_rows,
      dom_elements: res.dom_nodes,
      duration_ms: round(res.duration_ms),
      frames: f.length,
      frames_over_50ms: f.filter((x) => x > 50).length,
      frames_over_33ms: f.filter((x) => x > 33.4).length,
      frame_p50_ms: round(pct(f, 50), 1),
      frame_p95_ms: round(pct(f, 95), 1),
      frame_max_ms: round(Math.max(0, ...f), 1),
      avg_fps: round(f.length / (res.duration_ms / 1000), 1),
      react_commits: res.commits,
      long_tasks: res.long_tasks,
      long_task_ms: round(res.long_task_ms),
      guard_violations: net.violations,
    };
  } finally {
    await context.close();
  }
}

async function clickSwitch(page, name, readySrc) {
  // records the click timestamp in the page, clicks the MarketSwitch button, waits until readySrc holds
  await page.evaluate(() => {
    const P = window.__perf;
    P.clickAt = null;
    P.pressedAt = null;
    P.evMark = P.events.length;
    P.vtMark = P.vt.length;
    P.cMark = P.commits;
    document.addEventListener("click", (e) => (P.clickAt = e.timeStamp), { capture: true, once: true });
  });
  const btn = page.getByTestId("market-switch").getByRole("button", { name: new RegExp(`^${name}`) });
  await btn.click();
  const r = await page.evaluate(
    async ({ readySrc, name }) => {
      const P = window.__perf;
      const pressed = `(() => { const b = [...document.querySelectorAll('[data-testid="market-switch"] button')].find(x => x.querySelector('.mkt-name')?.textContent === ${JSON.stringify(name)}); return !!b && b.getAttribute('aria-pressed') === 'true'; })()`;
      const tPressed = await P.waitFor(pressed, 15000);
      const tReady = await P.waitFor(readySrc, 30000);
      await new Promise((r) => setTimeout(r, 700)); // let the view transition / event timing entries land
      const ev = P.events.slice(P.evMark).filter((e) => e[0] === "click");
      const vt = P.vt.slice(P.vtMark)[0];
      return {
        ui_ms: tPressed != null && P.clickAt != null ? tPressed - P.clickAt : null,
        content_ms: tReady != null && P.clickAt != null ? tReady - P.clickAt : null,
        click_event_duration_ms: ev.length ? Math.max(...ev.map((e) => e[2])) : null,
        click_processing_ms: ev.length ? Math.max(...ev.map((e) => e[4])) : null,
        view_transition_ms: vt && vt.finished != null && P.clickAt != null ? vt.finished - P.clickAt : null,
        commits: P.commits - P.cMark,
      };
    },
    { readySrc, name },
  );
  return r;
}

async function marketSwitchOnce(browser, profileName) {
  const { context, page, net } = await newPage(browser, profileName, { ready: ROUTE.dashboard_kr.ready, reactHook: "count" });
  try {
    await page.goto("/?m=kr", { waitUntil: "commit" });
    await waitContent(page, 30_000);
    await net.waitApiIdle(1500, 15_000);
    const steps = {};
    const seq = [
      ["cold_kr_to_us", "미장", KPI_US],
      ["cold_us_to_all", "전체", OV],
      ["warm_all_to_kr", "국장", KPI_KR],
      ["warm_kr_to_us", "미장", KPI_US],
      ["warm_us_to_all", "전체", OV],
    ];
    for (const [key, name, ready] of seq) {
      const n0 = [...net.reqs.values()].filter((r) => r.api).length;
      steps[key] = await clickSwitch(page, name, ready);
      await net.waitApiIdle(1000, 10_000);
      steps[key].api_requests = [...net.reqs.values()].filter((r) => r.api).length - n0;
      Object.keys(steps[key]).forEach((k) => (steps[key][k] = round(steps[key][k], 1)));
    }
    return { steps, guard_violations: net.violations };
  } finally {
    await context.close();
  }
}

async function suiteInteract(browser) {
  const out = { ...meta(), runs: RUNS, scroll_step_px: SCROLL_STEP_PX, room_scroll: {}, dashboard_chat_scroll: {}, market_switch: {} };
  for (const prof of ["desktop", "mobile_cpu4x"]) {
    for (const [outKey, routeKey] of [
      ["room_scroll", "room_kr"],
      ["dashboard_chat_scroll", "dashboard_kr"],
    ]) {
      const runs = [];
      for (let i = 0; i < RUNS; i++) {
        const r = await chatScrollOnce(browser, prof, routeKey);
        runs.push(r);
        console.log(
          `[interact] ${outKey} ${prof} #${i + 1}: ${r.frames} frames, >50ms ${r.frames_over_50ms}, p95 ${r.frame_p95_ms} ms, max ${r.frame_max_ms} ms, commits ${r.react_commits} (${r.scroller}, ${r.scroll_height}px)`,
        );
        if (r.guard_violations.length) throw new Error("guard violation");
      }
      out[outKey][prof] = {
        median: medianRecord(runs.map(({ ok, scroller, guard_violations, ...n }) => n)),
        scroller: runs[0].scroller,
        all_runs: runs.map(({ guard_violations, ...n }) => n),
      };
    }
    const sw = [];
    for (let i = 0; i < RUNS; i++) {
      const r = await marketSwitchOnce(browser, prof);
      sw.push(r.steps);
      console.log(`[interact] market switch ${prof} #${i + 1}: ${JSON.stringify(Object.fromEntries(Object.entries(r.steps).map(([k, v]) => [k, v.content_ms])))}`);
      if (r.guard_violations.length) throw new Error("guard violation");
    }
    out.market_switch[prof] = {};
    for (const step of Object.keys(sw[0])) {
      out.market_switch[prof][step] = medianRecord(sw.map((s) => s[step]));
    }
    out.market_switch[prof].all_runs = sw;
  }
  writeJson(path.join(rawDir(args.label), "fe_interaction.json"), out);
}

// ---------------------------------------------------------------- render (unminified build + vite preview)
async function waitHttp(url, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      const r = await fetch(url);
      if (r.ok) return true;
    } catch {
      /* not up yet */
    }
    await sleep(300);
  }
  return false;
}

async function suiteRender(browser) {
  const outDir = path.join(TMP, "fe_build_named");
  if (!args.reuse_build) {
    execSync(`npx vite build --minify false --outDir "${outDir}" --emptyOutDir --logLevel warn`, {
      cwd: FRONTEND,
      stdio: "inherit",
    });
  }
  const port = Number(args.render_port);
  const url = `http://127.0.0.1:${port}`;
  const srv = spawn(
    "npx",
    ["vite", "preview", "--outDir", outDir, "--port", String(port), "--strictPort", "--host", "127.0.0.1"],
    { cwd: FRONTEND, env: { ...process.env, VITE_BACKEND_URL: args.backend_url }, detached: true, stdio: "ignore" },
  );
  const seconds = Number(args.render_seconds);
  const out = {
    ...meta(),
    note: "unminified production build (vite build --minify false) served by vite preview; counts come from a React DevTools hook stub",
    preview_url: url,
    backend_url: args.backend_url,
    seconds,
    pages: {},
  };
  try {
    if (!(await waitHttp(`${url}/api/health`, 30_000))) throw new Error("vite preview did not start");
    for (const key of ["overview", "dashboard_kr", "room_kr"]) {
      console.log(`[render] ${key} ...`);
      // initial load render counts
      const { context, page, net } = await newPage(browser, "desktop", { ready: ROUTE[key].ready, reactHook: "walk", baseUrl: url });
      let load;
      try {
        await page.goto(ROUTE[key].path, { waitUntil: "commit" });
        await waitContent(page, 30_000);
        await net.waitApiIdle(1500, 15_000);
        load = await page.evaluate(() => ({ commits: window.__perf.commits, renders: window.__perf.renders, mounts: window.__perf.mounts }));
      } finally {
        await context.close();
      }
      const st = await steadyOnce(browser, ROUTE[key], seconds, { reactHook: "walk", baseUrl: url });
      const top = (o, n = 25) =>
        Object.fromEntries(
          Object.entries(o)
            .sort((a, b) => b[1] - a[1])
            .slice(0, n),
        );
      const total = (o) => Object.values(o).reduce((s, v) => s + v, 0);
      out.pages[key] = {
        load_commits: load.commits,
        load_component_renders_total: total(load.renders) + total(load.mounts),
        load_mounts_top: top(load.mounts, 15),
        load_rerenders_top: top(load.renders, 15),
        steady_commits_per_min: st.react_commits_per_min,
        steady_component_renders_per_min: round((total(st.renders) * 60) / seconds, 1),
        steady_component_mounts_per_min: round((total(st.mounts) * 60) / seconds, 1),
        steady_rerenders_per_min_top: Object.fromEntries(
          Object.entries(top(st.renders)).map(([k, v]) => [k, round((v * 60) / seconds, 1)]),
        ),
        steady_mounts_per_min_top: Object.fromEntries(
          Object.entries(top(st.mounts, 10)).map(([k, v]) => [k, round((v * 60) / seconds, 1)]),
        ),
        steady_ws_by_channel_per_min: st.ws_by_channel_per_min,
        steady_api_requests_per_min: st.api_requests_per_min,
      };
      console.log(`[render] ${key}: ${out.pages[key].steady_component_renders_per_min} component renders/min`);
    }
  } finally {
    try {
      process.kill(-srv.pid, "SIGTERM");
    } catch {
      /* already gone */
    }
  }
  writeJson(path.join(rawDir(args.label), "fe_render.json"), out);
}

// ---------------------------------------------------------------- main
const browser = await chromium.launch({ headless: true });
BROWSER_VERSION = browser.version();
try {
  for (const s of suites) {
    const t0 = Date.now();
    if (s === "load") await suiteLoad(browser);
    else if (s === "steady") await suiteSteady(browser);
    else if (s === "interact") await suiteInteract(browser);
    else if (s === "render") await suiteRender(browser);
    else throw new Error(`unknown suite ${s}`);
    console.log(`[${s}] done in ${((Date.now() - t0) / 1000).toFixed(0)} s`);
  }
} finally {
  await browser.close();
}
