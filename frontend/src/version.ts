import { toast } from "./ui/toast";

/*
 * 재배포 감지 → 열려 있는 화면 자동 새로고침.
 *
 * - 화면 배포: /index.html 을 다시 받아(no-cache — 안 바뀌었으면 304) 입구 스크립트 이름(내용 해시가 붙은
 *   /assets/index-….js)이 지금 화면의 것과 다르면 새 배포다
 * - 서버 배포: /api/health 의 boot(서버가 뜰 때마다 새로 정하는 값)가 처음 본 값과 다르면 서버가 다시 떴다
 * 둘 중 하나라도 바뀌면 새로고침한다.
 * - 글을 쓰는 중(입력 칸에 글자가 있고 초점이 있음)이거나 창(dialog)이 열려 있으면 끝날 때까지 기다린다
 * - 화면이 숨어 있으면(다른 탭·앱 전환) 바로, 보이고 있으면 안내를 잠깐 띄운 뒤 새로고침한다
 * - 새로고침 직후 또 바뀐 것으로 보이면(캐시 등) 1분 안에는 다시 하지 않는다 (무한 새로고침 방지)
 * 개발 서버(npm run dev)에서는 켜지 않는다 — Vite 가 알아서 바꿔 끼운다.
 */

export const POLL_MS = 30_000;
const FIRST_CHECK_MS = 5_000;
const HIDDEN_POLL_MS = 120_000;
const BUSY_RECHECK_MS = 5_000;
const NOTICE_MS = 1_500;
const GUARD_KEY = "deploy-reload-at";
const GUARD_MS = 60_000;

/** index.html 글에서 입구 모듈 스크립트 주소 (없으면 null) */
export function entryScript(html: string): string | null {
  const m = /<script\b[^>]*\btype=["']module["'][^>]*\bsrc=["']([^"']+)["']/i.exec(html);
  return m ? m[1] : null;
}

/** 지금 화면이 받은 입구 스크립트 주소 */
export function currentEntry(doc: Document = document): string | null {
  return doc.querySelector('script[type="module"][src]')?.getAttribute("src") ?? null;
}

export interface VersionProbe {
  /** /index.html 글 (못 받으면 null) */
  index: () => Promise<string | null>;
  /** /api/health 의 boot (못 받으면 null — 서버가 다시 뜨는 중) */
  boot: () => Promise<string | null>;
}

/**
 * 한 번 확인할 때마다 '바뀌었는지'를 돌려주는 함수. 처음 받은 서버 boot 를 기억해 두고 비교한다.
 * 받지 못한 값(네트워크·재시작 중)은 바뀐 것으로 보지 않는다.
 */
export function makeVersionCheck(probe: VersionProbe, entry: string | null): () => Promise<boolean> {
  let firstBoot: string | null = null;
  return async () => {
    const [html, boot] = await Promise.all([probe.index().catch(() => null), probe.boot().catch(() => null)]);
    const nextEntry = html ? entryScript(html) : null;
    const uiChanged = entry !== null && nextEntry !== null && nextEntry !== entry;
    if (boot && firstBoot === null) firstBoot = boot;
    const serverChanged = boot !== null && firstBoot !== null && boot !== firstBoot;
    return uiChanged || serverChanged;
  };
}

/** 사용자가 무언가 하는 중인지: 글자가 든 입력 칸에 초점이 있거나, 창(dialog)이 열려 있음 */
export function isBusy(doc: Document = document): boolean {
  const a = doc.activeElement as HTMLElement | null;
  if (a?.isContentEditable) return true;
  if (a && (a.tagName === "INPUT" || a.tagName === "TEXTAREA")) {
    const v = (a as HTMLInputElement | HTMLTextAreaElement).value;
    if (v && v.length > 0) return true;
  }
  return doc.querySelector('[role="dialog"], [role="alertdialog"]') !== null;
}

/** 방금(1분 안) 이 이유로 새로고침했으면 false. 아니면 기록하고 true */
export function mayReload(storage: Pick<Storage, "getItem" | "setItem"> | null, now = Date.now()): boolean {
  if (!storage) return false; // 기록을 못 남기면 새로고침이 되풀이될 수 있어 하지 않는다
  try {
    const last = Number(storage.getItem(GUARD_KEY)) || 0;
    if (now - last < GUARD_MS) return false;
    storage.setItem(GUARD_KEY, String(now));
    return true;
  } catch {
    return false; // 기록을 못 남기면 새로고침이 되풀이될 수 있어 하지 않는다
  }
}

const session = (): Storage | null => {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
};

async function fetchIndex(): Promise<string | null> {
  const r = await fetch("/index.html", { cache: "no-cache", credentials: "same-origin" });
  return r.ok ? r.text() : null;
}

async function fetchBoot(): Promise<string | null> {
  const r = await fetch("/api/health", { cache: "no-store", credentials: "same-origin" });
  if (!r.ok) return null;
  const body = (await r.json()) as { boot?: unknown };
  return typeof body.boot === "string" && body.boot ? body.boot : null;
}

/** 앱이 뜰 때 한 번 부른다 (main.tsx). 멈추는 함수를 돌려준다 */
export function startVersionWatch(): () => void {
  if (typeof window === "undefined") return () => {};
  // 빌드한 화면만 (입구가 /assets/index-….js). 개발 서버(/src/main.tsx)·테스트에서는 켜지 않는다
  const entry = currentEntry();
  if (!entry?.startsWith("/assets/")) return () => {};
  const check = makeVersionCheck({ index: fetchIndex, boot: fetchBoot }, entry);
  let timer = 0;
  let pending = false; // 새 배포를 봤고 새로고침을 기다리는 중
  let stopped = false;

  const reloadWhenFree = () => {
    if (stopped) return;
    if (document.visibilityState === "hidden") {
      if (mayReload(session())) window.location.reload();
      return;
    }
    if (isBusy()) {
      timer = window.setTimeout(reloadWhenFree, BUSY_RECHECK_MS);
      return;
    }
    if (!mayReload(session())) return;
    toast.info("새 버전이 배포돼 화면을 새로고침해요", NOTICE_MS);
    window.setTimeout(() => window.location.reload(), NOTICE_MS);
  };

  const tick = async () => {
    if (stopped || pending) return;
    window.clearTimeout(timer);
    try {
      if (await check()) {
        pending = true;
        reloadWhenFree();
        return;
      }
    } catch {
      /* 다음 차례에 다시 */
    }
    if (!stopped)
      timer = window.setTimeout(tick, document.visibilityState === "hidden" ? HIDDEN_POLL_MS : POLL_MS);
  };

  // 다시 보이거나 네트워크가 돌아오면 바로 확인 (휴대폰에서 앱으로 돌아왔을 때)
  const onShow = () => {
    if (document.visibilityState === "visible") void tick();
  };
  document.addEventListener("visibilitychange", onShow);
  window.addEventListener("online", onShow);
  // 첫 확인은 조금 뒤에 (서버 boot 를 기억해 두는 첫 값 — 화면이 다 뜬 다음)
  timer = window.setTimeout(tick, FIRST_CHECK_MS);
  return () => {
    stopped = true;
    window.clearTimeout(timer);
    document.removeEventListener("visibilitychange", onShow);
    window.removeEventListener("online", onShow);
  };
}
