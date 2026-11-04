#!/usr/bin/env node
// Frontend bundle size baseline. Builds the app into perf/tmp/fe_build (git-ignored) with sourcemaps +
// manifest, WITHOUT touching frontend/vite.config.ts or frontend/dist, then reports per-chunk raw/gzip/brotli
// sizes, totals, code-splitting facts and bytes per npm package (attributed through the sourcemaps).
// Also records what the live nginx actually serves (Content-Encoding / Cache-Control / size).
//
//   node perf/fe_bundle.mjs [--label before] [--base-url http://localhost:15173]
//   -> docs/perf/raw/<label>/fe_bundle.json

import { execSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { FRONTEND, TMP, gitInfo, parseArgs, rawDir, round, writeJson } from "./fe_lib.mjs";

const args = parseArgs(process.argv.slice(2), { label: "before", base_url: "http://localhost:15173" });
const OUT = path.join(TMP, "fe_build");

// ---------- 1) build ----------
const t0 = Date.now();
const log = execSync(
  `npx vite build --outDir "${OUT}" --emptyOutDir --sourcemap --manifest --logLevel info`,
  { cwd: FRONTEND, stdio: ["ignore", "pipe", "pipe"], env: { ...process.env, NO_COLOR: "1" } },
).toString();
const buildWall = (Date.now() - t0) / 1000;
const warnings = (log.match(/\(!\)[^\n]*/g) ?? []).map((s) => s.trim());

// ---------- 2) chunks ----------
const gz = (b) => zlib.gzipSync(b, { level: 9 }).length;
const br = (b) =>
  zlib.brotliCompressSync(b, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 11 } }).length;

const manifest = JSON.parse(fs.readFileSync(path.join(OUT, ".vite", "manifest.json"), "utf8"));
const byFile = {};
for (const [src, m] of Object.entries(manifest)) {
  byFile[m.file] = { src, ...m };
  for (const css of m.css ?? []) byFile[css] ??= { src: `${src} (css)`, file: css };
}
const entryKeys = Object.entries(manifest).filter(([, m]) => m.isEntry);
const initial = new Set();
const visit = (key) => {
  const m = manifest[key];
  if (!m || initial.has(m.file)) return;
  initial.add(m.file);
  (m.css ?? []).forEach((c) => initial.add(c));
  (m.imports ?? []).forEach(visit);
};
entryKeys.forEach(([k]) => visit(k));

const chunks = [];
for (const f of fs.readdirSync(path.join(OUT, "assets")).sort()) {
  if (f.endsWith(".map")) continue;
  const rel = `assets/${f}`;
  const buf = fs.readFileSync(path.join(OUT, rel));
  const m = byFile[rel] ?? {};
  const type = f.endsWith(".js") ? "js" : f.endsWith(".css") ? "css" : "asset";
  chunks.push({
    file: rel.replace(/-[\w-]{8}\./, "-[hash]."),
    file_hashed: rel,
    type,
    raw: buf.length,
    gzip: type === "asset" ? null : gz(buf),
    brotli: type === "asset" ? null : br(buf),
    is_entry: !!m.isEntry,
    is_dynamic_entry: !!m.isDynamicEntry,
    initial: initial.has(rel),
    source: m.src ?? null,
  });
}
const sum = (xs, k) => xs.reduce((s, x) => s + (x[k] ?? 0), 0);
const js = chunks.filter((c) => c.type === "js");
const css = chunks.filter((c) => c.type === "css");
const initJs = js.filter((c) => c.initial);
const initCss = css.filter((c) => c.initial);
const totals = {
  js_chunks: js.length,
  css_files: css.length,
  js_raw: sum(js, "raw"),
  js_gzip: sum(js, "gzip"),
  js_brotli: sum(js, "brotli"),
  css_raw: sum(css, "raw"),
  css_gzip: sum(css, "gzip"),
  css_brotli: sum(css, "brotli"),
  initial_js_raw: sum(initJs, "raw"),
  initial_js_gzip: sum(initJs, "gzip"),
  initial_css_raw: sum(initCss, "raw"),
  initial_css_gzip: sum(initCss, "gzip"),
  largest_js_raw: Math.max(0, ...js.map((c) => c.raw)),
  largest_js_gzip: Math.max(0, ...js.map((c) => c.gzip)),
  public_assets_raw: fs
    .readdirSync(path.join(FRONTEND, "public"))
    .reduce((s, f) => s + fs.statSync(path.join(FRONTEND, "public", f)).size, 0),
};

// ---------- 3) bytes per package via sourcemaps ----------
const B64 = Object.fromEntries([..."ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"].map((c, i) => [c, i]));
function decodeSegment(seg) {
  const out = [];
  let v = 0,
    shift = 0;
  for (const ch of seg) {
    const d = B64[ch];
    v += (d & 31) << shift;
    if (d & 32) shift += 5;
    else {
      out.push(v & 1 ? -(v >>> 1) : v >>> 1);
      v = 0;
      shift = 0;
    }
  }
  return out;
}
function attribute(code, map) {
  const lines = code.split("\n");
  const mlines = map.mappings.split(";");
  const bytes = new Map(); // source index (-1 = unmapped) -> bytes
  const add = (k, n) => n > 0 && bytes.set(k, (bytes.get(k) ?? 0) + n);
  let src = 0;
  for (let li = 0; li < lines.length; li++) {
    const text = lines[li];
    const segs = (mlines[li] ?? "").split(",").filter(Boolean);
    let col = 0,
      prevCol = 0,
      prevSrc = -1;
    for (const s of segs) {
      const f = decodeSegment(s);
      col += f[0];
      add(prevSrc, Buffer.byteLength(text.slice(prevCol, col)));
      if (f.length >= 4) {
        src += f[1];
        prevSrc = src;
      } else prevSrc = -1;
      prevCol = col;
    }
    add(prevSrc, Buffer.byteLength(text.slice(prevCol)) + 1);
  }
  const out = new Map();
  for (const [k, n] of bytes) {
    const name = k < 0 ? "[unmapped]" : map.sources[k];
    out.set(name, (out.get(name) ?? 0) + n);
  }
  return out;
}

const pkgOf = (s) => {
  const p = s.replace(/\\/g, "/");
  const i = p.lastIndexOf("node_modules/");
  if (i >= 0) {
    const rest = p.slice(i + 13).split("/");
    return rest[0].startsWith("@") ? `${rest[0]}/${rest[1]}` : rest[0];
  }
  if (p.includes("/src/") || p.startsWith("src/") || p.startsWith("../src/")) return "app (src/)";
  if (p === "[unmapped]") return "[unmapped]";
  return `other: ${p.split("/").slice(-2).join("/")}`;
};
const byPkg = new Map();
const bySrc = new Map();
let mappedTotal = 0;
for (const c of js) {
  const mapFile = path.join(OUT, c.file_hashed + ".map");
  if (!fs.existsSync(mapFile)) continue;
  const code = fs.readFileSync(path.join(OUT, c.file_hashed), "utf8");
  const map = JSON.parse(fs.readFileSync(mapFile, "utf8"));
  for (const [s, n] of attribute(code, map)) {
    mappedTotal += n;
    const pkg = pkgOf(s);
    byPkg.set(pkg, (byPkg.get(pkg) ?? 0) + n);
    if (pkg === "app (src/)") {
      const short = s.replace(/^.*?src\//, "src/");
      bySrc.set(short, (bySrc.get(short) ?? 0) + n);
    }
  }
}
const sorted = (m) =>
  [...m.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([name, bytes]) => ({ name, bytes, pct: round((bytes / mappedTotal) * 100, 1) }));

// ---------- 4) code splitting facts ----------
const srcFiles = [];
const walk = (d) =>
  fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
    const p = path.join(d, e.name);
    if (e.isDirectory()) walk(p);
    else if (/\.(tsx?|jsx?)$/.test(e.name) && !p.includes("__tests__")) srcFiles.push(p);
  });
walk(path.join(FRONTEND, "src"));
let lazyCalls = 0,
  dynImports = 0;
for (const f of srcFiles) {
  const t = fs.readFileSync(f, "utf8");
  lazyCalls += (t.match(/\blazy\s*\(/g) ?? []).length;
  dynImports += (t.match(/\bimport\s*\(/g) ?? []).length;
}
const app = fs.readFileSync(path.join(FRONTEND, "src", "App.tsx"), "utf8");
const pageImportsStatic = (app.match(/^import\s+\{[^}]+\}\s+from\s+"\.\/pages\//gm) ?? []).length;

// ---------- 5) what the live server serves ----------
async function served() {
  try {
    const html = await (await fetch(args.base_url)).text();
    const urls = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
    const out = [];
    for (const u of urls) {
      const r = await fetch(new URL(u, args.base_url), { headers: { "Accept-Encoding": "gzip, br" } });
      const body = Buffer.from(await r.arrayBuffer());
      out.push({
        url: u.replace(/-[\w-]{8}\./, "-[hash]."),
        content_encoding: r.headers.get("content-encoding"),
        cache_control: r.headers.get("cache-control"),
        etag: !!r.headers.get("etag"),
        bytes_on_wire: Number(r.headers.get("content-length")) || null,
        bytes_decoded: body.length,
      });
    }
    return { base_url: args.base_url, assets: out, html_bytes: Buffer.byteLength(html) };
  } catch (e) {
    return { base_url: args.base_url, error: String(e) };
  }
}

const result = {
  label: args.label,
  generated_at: new Date().toISOString(),
  git: gitInfo(),
  build: { command: "npx vite build --outDir perf/tmp/fe_build --emptyOutDir --sourcemap --manifest", wall_s: round(buildWall, 2), warnings },
  totals,
  chunks: chunks.map(({ file_hashed, ...c }) => c),
  code_split: {
    routes_lazy: lazyCalls > 0,
    react_lazy_calls: lazyCalls,
    dynamic_imports_in_src: dynImports,
    dynamic_entry_chunks: chunks.filter((c) => c.is_dynamic_entry).length,
    pages_imported_statically_in_app_tsx: pageImportsStatic,
  },
  by_package: sorted(byPkg).slice(0, 25),
  app_src_top_files: sorted(bySrc).slice(0, 20),
  mapped_bytes_total: mappedTotal,
  served: await served(),
};
writeJson(path.join(rawDir(args.label), "fe_bundle.json"), result);
console.log(
  `JS ${totals.js_raw} B raw / ${totals.js_gzip} B gzip in ${totals.js_chunks} chunk(s); CSS ${totals.css_raw} B; build ${buildWall.toFixed(1)} s`,
);
