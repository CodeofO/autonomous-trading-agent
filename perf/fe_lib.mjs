// Shared helpers for the frontend measurement kit (perf/fe_*.mjs). Node >= 20, no new dependencies:
// Playwright is loaded from frontend/node_modules (@playwright/test is already a devDependency).
//
// SAFETY: every page opened by this kit gets GUARD_SCRIPT, which makes window.fetch / XMLHttpRequest /
// navigator.sendBeacon refuse anything that is not GET/HEAD/OPTIONS. A CDP listener additionally records
// any non-GET request that still reaches the network (guard_violations; must stay 0).

import { createRequire } from "node:module";
import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const PERF_DIR = path.dirname(fileURLToPath(import.meta.url));
export const ROOT = path.resolve(PERF_DIR, "..");
export const FRONTEND = path.join(ROOT, "frontend");
export const TMP = path.join(PERF_DIR, "tmp"); // git-ignored (tmp/)

export function loadPlaywright() {
  const req = createRequire(path.join(FRONTEND, "package.json"));
  return req("@playwright/test");
}

export function parseArgs(argv, defaults) {
  const out = { ...defaults };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith("--")) continue;
    const k = a.slice(2).replace(/-/g, "_");
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[k] = true;
    else {
      out[k] = next;
      i++;
    }
  }
  return out;
}

export const rawDir = (label) => path.join(ROOT, "docs", "perf", "raw", label);

export function writeJson(file, data) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n");
  console.log(`wrote ${path.relative(ROOT, file)}`);
}

export function readJson(file, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return fallback;
  }
}

export function gitInfo() {
  const sh = (c) => {
    try {
      return execSync(c, { cwd: ROOT, stdio: ["ignore", "pipe", "ignore"] }).toString().trim();
    } catch {
      return null;
    }
  };
  const status = sh("git status --porcelain -- frontend") ?? "";
  return {
    head: sh("git rev-parse --short HEAD"),
    branch: sh("git rev-parse --abbrev-ref HEAD"),
    frontend_uncommitted_paths: status ? status.split("\n").length : 0,
  };
}

// ---------- statistics ----------
export const round = (v, d = 1) => (v == null || !Number.isFinite(v) ? null : Math.round(v * 10 ** d) / 10 ** d);

export function median(xs) {
  const v = xs.filter((x) => x != null && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

export function pct(xs, p) {
  const v = xs.filter((x) => x != null && Number.isFinite(x)).sort((a, b) => a - b);
  if (!v.length) return null;
  return v[Math.min(v.length - 1, Math.ceil((p / 100) * v.length) - 1)];
}

/** median of every numeric field across runs (non-numeric fields: first run's value) */
export function medianRecord(runs) {
  const out = {};
  const keys = new Set(runs.flatMap((r) => Object.keys(r)));
  for (const k of keys) {
    const vals = runs.map((r) => r[k]);
    if (vals.every((v) => v == null || typeof v === "number")) out[k] = round(median(vals), /cls/.test(k) ? 4 : 1);
    else out[k] = vals.find((v) => v != null) ?? null;
  }
  return out;
}

// ---------- routes ----------
// key -> path + "content visible" predicate (evaluated in the page every animation frame).
// Keys are the comparison keys: keep them stable between BEFORE and AFTER (update the predicate if markup changes).
const kpiLoaded = `(() => { const v = document.querySelector('.dashboard .kpis .kpi-value'); const t = v && v.textContent.trim(); return !!t && t !== '—'; })()`;
const kpiUsd = `(() => { const v = document.querySelector('.dashboard .kpis .kpi-value'); const t = v && v.textContent.trim(); return !!t && t.includes('$'); })()`;
const chatLoaded = `!!document.querySelector('[data-testid="chat-scroll"] .chat-row, [data-testid="chat-scroll"] [data-testid="session"], [data-testid="chat-scroll"] .chat-divider')`;
export const ROUTES = [
  { key: "overview", path: "/?m=all", ready: `!!document.querySelector('[data-testid="ov-total"]')` },
  { key: "dashboard_kr", path: "/?m=kr", ready: kpiLoaded },
  { key: "room_kr", path: "/room", ready: chatLoaded },
  {
    key: "portfolio_kr",
    path: "/portfolio",
    ready: `!!document.querySelector('.stack > section.card:last-child table tbody tr')`,
  },
  { key: "agent_kr", path: "/agent", ready: `!!document.querySelector('.agent-hero')` },
  {
    // 화면 2단계부터 설정은 주제별 화면: /settings 는 넓은 화면이면 목록 + LLM 모델, 휴대폰이면 목록(지금 값 한 줄씩).
    // (그 전에는 한 화면에 모든 카드 — 리스크 폼까지 채워지면 '보임'이었다. 키는 그대로, 비교할 때 유의)
    key: "settings_kr",
    path: "/settings",
    ready: `(() => { const hub = document.querySelector('[data-testid="settings-hub"]'); if (!hub) return false; if (matchMedia('(max-width: 760px)').matches) return /시세 연결/.test(hub.textContent); return !!document.querySelector('[aria-label="LLM 모델 선택"] button, [data-testid="settings-pane"] .card .error'); })()`,
  },
  { key: "memo_kr", path: "/memo", ready: `!!document.querySelector('[data-testid^="memo-count-"]')` },
  {
    key: "costs",
    path: "/costs",
    ready: `!!document.querySelector('[data-testid="projection"], [data-testid="costs-empty"]')`,
  },
  { key: "stock_kr", path: "/stock/005930", ready: `!!document.querySelector('[data-testid="candle-chart"] canvas')` },
  { key: "logs_kr", path: "/logs", ready: `!!document.querySelector('section.card table tbody tr')` },
  { key: "dashboard_us", path: "/?m=us", ready: kpiUsd },
  { key: "room_us", path: "/room?m=us", ready: chatLoaded },
  {
    key: "stock_us",
    path: "/stock/AAPL?m=us",
    ready: `!!document.querySelector('[data-testid="candle-chart"] canvas')`,
  },
];
export const ROUTE = Object.fromEntries(ROUTES.map((r) => [r.key, r]));

// ---------- device / network profiles ----------
// DevTools "Fast 3G" preset: 562.5 ms RTT, ~1.44 Mbit/s down, ~675 kbit/s up
export const FAST_3G = {
  offline: false,
  latency: 562.5,
  downloadThroughput: Math.round(((1.6 * 1024 * 1024) / 8) * 0.9),
  uploadThroughput: Math.round(((750 * 1024) / 8) * 0.9),
};

const ANDROID_UA =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36";

export const PROFILES = {
  desktop: { context: { viewport: { width: 1440, height: 900 } }, cpu: 1, network: null },
  mobile_cpu4x: {
    context: {
      viewport: { width: 375, height: 812 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
      userAgent: ANDROID_UA,
    },
    cpu: 4,
    network: null,
  },
  mobile_cpu4x_fast3g: {
    context: {
      viewport: { width: 375, height: 812 },
      deviceScaleFactor: 3,
      isMobile: true,
      hasTouch: true,
      userAgent: ANDROID_UA,
    },
    cpu: 4,
    network: FAST_3G,
  },
};

// ---------- in-page scripts ----------
export const GUARD_SCRIPT = `(() => {
  const SAFE = new Set(['GET', 'HEAD', 'OPTIONS']);
  window.__perfBlocked = [];
  const block = (method, url) => { window.__perfBlocked.push({ method, url: String(url) }); };
  const of = window.fetch;
  window.fetch = function (input, init) {
    const method = String((init && init.method) || (input && typeof input === 'object' && 'method' in input ? input.method : 'GET')).toUpperCase();
    if (!SAFE.has(method)) {
      const url = typeof input === 'string' ? input : (input && input.url) || String(input);
      block(method, url);
      return Promise.reject(new TypeError('perf guard: blocked ' + method + ' ' + url));
    }
    return of.apply(this, arguments);
  };
  const xo = XMLHttpRequest.prototype.open, xs = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) { this.__perfMethod = String(m).toUpperCase(); this.__perfUrl = u; return xo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    if (!SAFE.has(this.__perfMethod)) { block(this.__perfMethod, this.__perfUrl); throw new TypeError('perf guard: blocked'); }
    return xs.apply(this, arguments);
  };
  if (navigator.sendBeacon) navigator.sendBeacon = (u) => { block('BEACON', u); return false; };
})();`;

/** web-vitals style collectors + "content visible" detector + optional React commit counter */
export function metricsScript({ ready = "false", reactHook = "count" } = {}) {
  return `(() => {
  const P = (window.__perf = { lcp: null, lcpSize: null, lcpTag: null, shifts: [], longtasks: [], contentAt: null,
    commits: 0, commitTimes: [], renders: {}, mounts: {}, canvasAdded: 0, vt: [], events: [] });
  const desc = (el) => el ? (el.tagName.toLowerCase() + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '')) : null;
  const obs = (type, fn, extra) => { try { new PerformanceObserver((l) => l.getEntries().forEach(fn)).observe({ type, buffered: true, ...(extra || {}) }); } catch (e) {} };
  obs('largest-contentful-paint', (e) => { P.lcp = e.startTime; P.lcpSize = e.size; P.lcpTag = desc(e.element); });
  obs('layout-shift', (e) => { if (!e.hadRecentInput) P.shifts.push([e.startTime, e.value]); });
  obs('longtask', (e) => { P.longtasks.push([e.startTime, e.duration]); });
  obs('event', (e) => { if (e.name === 'click' || e.name === 'pointerdown' || e.name === 'pointerup') P.events.push([e.name, e.startTime, e.duration, e.processingStart - e.startTime, e.processingEnd - e.processingStart]); }, { durationThreshold: 16 });
  // content visible: first animation frame where the route's predicate holds and the match is laid out
  const ready = () => { try { return ${ready}; } catch (e) { return false; } };
  const tick = () => { if (P.contentAt == null && ready()) { P.contentAt = performance.now(); return; } if (performance.now() < 120000) requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  P.waitFor = (src, timeout) => new Promise((res) => {
    const f = new Function('return (' + src + ')'); const t0 = performance.now();
    const step = () => { let ok = false; try { ok = f(); } catch (e) {} if (ok) return res(performance.now()); if (performance.now() - t0 > timeout) return res(null); requestAnimationFrame(step); };
    step();
  });
  // canvas insertions (lightweight-charts creates canvases; a re-created chart adds new ones)
  new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) { if (n.nodeType !== 1) continue; if (n.tagName === 'CANVAS') P.canvasAdded++; else if (n.querySelectorAll) P.canvasAdded += n.querySelectorAll('canvas').length; } })
    .observe(document, { childList: true, subtree: true });
  // View Transitions (MarketSwitch / mobile tabs): record start and finish
  if (typeof document.startViewTransition === 'function') {
    const svt = document.startViewTransition.bind(document);
    document.startViewTransition = (cb) => { const rec = { start: performance.now(), ready: null, finished: null }; P.vt.push(rec); const t = svt(cb);
      t.ready.then(() => { rec.ready = performance.now(); }, () => {}); t.finished.then(() => { rec.finished = performance.now(); }, () => { rec.finished = performance.now(); }); return t; };
  }
  // React DevTools hook stub: production React calls onCommitFiberRoot on every commit
  const MODE = ${JSON.stringify(reactHook)};
  if (MODE !== 'off') {
    const COMP = new Set([0, 1, 11, 14, 15]);
    const nameOf = (f) => { const t = f.type; if (!t) return '?'; if (typeof t === 'function') return t.displayName || t.name || 'Anonymous';
      if (t.displayName) return t.displayName; if (t.render) return t.render.displayName || t.render.name || 'ForwardRef'; if (t.type) return t.type.displayName || t.type.name || 'Memo'; return '?'; };
    const bump = (o, k) => { o[k] = (o[k] || 0) + 1; };
    const mountAll = (f) => { for (let c = f; c; c = c.sibling) { if (COMP.has(c.tag)) bump(P.mounts, nameOf(c)); if (c.child) mountAll(c.child); } };
    // DevTools-style diff: only descend where the child pointer changed (bailed-out subtrees keep old fibers)
    const update = (next, prev) => {
      if (COMP.has(next.tag) && next !== prev && (next.flags & 1)) bump(P.renders, nameOf(next));
      if (next.child === prev.child) return;
      for (let c = next.child; c; c = c.sibling) { if (c.alternate) update(c, c.alternate); else { if (COMP.has(c.tag)) bump(P.mounts, nameOf(c)); if (c.child) mountAll(c.child); } }
    };
    window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
      supportsFiber: true, renderers: new Map(), isDisabled: false,
      inject() { return 1; }, checkDCE() {}, onScheduleFiberRoot() {}, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {},
      onCommitFiberRoot(id, root) {
        P.commits++; P.commitTimes.push(performance.now());
        if (MODE !== 'walk') return;
        try { const cur = root.current, prev = cur.alternate; if (!prev) { if (cur.child) mountAll(cur.child); } else update(cur, prev); } catch (e) {}
      },
    };
  }
})();`;
}

/** layout-shift entries -> CLS (largest session window: gap < 1 s, window < 5 s) */
export function clsOf(shifts) {
  let best = 0,
    cur = 0,
    start = -1,
    last = -1;
  for (const [t, v] of shifts) {
    if (start < 0 || t - last > 1000 || t - start > 5000) {
      cur = 0;
      start = t;
    }
    cur += v;
    last = t;
    best = Math.max(best, cur);
  }
  return best;
}

export function longTaskStats(tasks, from = 0, to = Infinity) {
  const sel = tasks.filter(([t]) => t >= from && t < to);
  return {
    long_tasks: sel.length,
    long_task_ms: round(
      sel.reduce((s, [, d]) => s + d, 0),
      1,
    ),
    long_task_max_ms: round(Math.max(0, ...sel.map(([, d]) => d)), 1),
    blocking_ms: round(
      sel.reduce((s, [, d]) => s + Math.max(0, d - 50), 0),
      1,
    ),
  };
}

/** /api/us/chat?limit=200 -> "/api/us/chat" ; numeric/symbol segments collapsed */
export function endpointKey(url) {
  try {
    const u = new URL(url);
    return u.pathname.replace(/\/(\d+|[A-Z0-9]{4,6})(?=\/|$)/g, "/:id");
  } catch {
    return url;
  }
}

/**
 * Attach a CDP session that tracks network + websocket traffic for one page.
 * Returns { cdp, net } where net exposes counters and helpers.
 */
export async function attachCdp(context, page, { cpu = 1, network = null } = {}) {
  const cdp = await context.newCDPSession(page);
  const net = {
    reqs: new Map(), // id -> {url, type, method, start, end, bytes, api}
    violations: [],
    ws: { frames: 0, bytes: 0, byChannel: {} },
    apiInflight: 0,
    lastApiActivity: 0,
    now: () => Date.now(),
  };
  await cdp.send("Network.enable");
  await cdp.send("Performance.enable", { timeDomain: "timeTicks" }).catch(() => {});
  if (cpu > 1) await cdp.send("Emulation.setCPUThrottlingRate", { rate: cpu });
  if (network) await cdp.send("Network.emulateNetworkConditions", network);
  cdp.on("Network.requestWillBeSent", (e) => {
    if (e.redirectResponse) return;
    const api = /\/api\//.test(e.request.url);
    const method = e.request.method;
    if (api && !["GET", "HEAD", "OPTIONS"].includes(method)) net.violations.push({ method, url: e.request.url });
    net.reqs.set(e.requestId, { url: e.request.url, type: e.type, method, start: Date.now(), end: null, bytes: 0, api });
    if (api) {
      net.apiInflight++;
      net.lastApiActivity = Date.now();
    }
  });
  const done = (e, failed) => {
    const r = net.reqs.get(e.requestId);
    if (!r || r.end) return;
    r.end = Date.now();
    r.failed = failed;
    if (!failed) r.bytes = e.encodedDataLength ?? 0;
    if (r.api) {
      net.apiInflight = Math.max(0, net.apiInflight - 1);
      net.lastApiActivity = Date.now();
    }
  };
  cdp.on("Network.loadingFinished", (e) => done(e, false));
  cdp.on("Network.loadingFailed", (e) => done(e, true));
  cdp.on("Network.webSocketFrameReceived", (e) => {
    net.ws.frames++;
    const p = e.response?.payloadData ?? "";
    net.ws.bytes += p.length;
    let ch = "?";
    try {
      ch = JSON.parse(p).channel ?? "?";
    } catch {
      /* not JSON */
    }
    net.ws.byChannel[ch] = (net.ws.byChannel[ch] ?? 0) + 1;
  });
  net.resetWs = () => {
    net.ws = { frames: 0, bytes: 0, byChannel: {} };
  };
  /** wait until no /api request is in flight and none started/finished for quietMs (max maxMs) */
  net.waitApiIdle = async (quietMs = 1500, maxMs = 15000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < maxMs) {
      if (net.apiInflight === 0 && Date.now() - net.lastApiActivity >= quietMs) return Date.now() - t0;
      await new Promise((r) => setTimeout(r, 100));
    }
    return null;
  };
  net.perfMetrics = async () => {
    const { metrics } = await cdp.send("Performance.getMetrics");
    return Object.fromEntries(metrics.map((m) => [m.name, m.value]));
  };
  net.gc = () => cdp.send("HeapProfiler.collectGarbage").catch(() => {});
  return { cdp, net };
}

export async function serverState(baseUrl) {
  const get = async (p) => {
    try {
      const r = await fetch(new URL(p, baseUrl), { method: "GET" });
      return r.ok ? r.json() : null;
    } catch {
      return null;
    }
  };
  const markets = (await get("/api/markets")) ?? [];
  const out = {};
  for (const m of markets) {
    out[m.code] = {
      enabled: m.enabled,
      open_now: m.open_now,
      agent_running: m.agent_running,
      engine_status: m.engine_status,
    };
  }
  return out;
}

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
