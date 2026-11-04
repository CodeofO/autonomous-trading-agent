/*
 * 휴대폰 가로 넘기기 (스와이프)
 *
 * - 하단 탭 화면(홈·채팅·자산·뉴스)에서 왼쪽/오른쪽으로 넘기면 옆 탭으로
 * - 그 밖의 화면(종목 상세·설정 주제·더보기 화면)에서 오른쪽으로 넘기면 뒤로
 * - 손가락 아래에 가로로 스크롤되는 상자(지표 띠·표)·입력칸·차트·시트가 있으면 넘기지 않는다
 *   (그 상자 자체의 가로 스크롤이 먼저)
 * - 넘기는 동안 본문이 손가락을 조금 따라온다(--swipe-x). '동작 줄이기'면 따라오지 않는다
 */

/** 이만큼 옆으로 움직여야 넘기기로 본다(px) */
export const SWIPE_MIN_PX = 64;
/** 이보다 오래 끌면(천천히 읽으며 움직임) 넘기기로 보지 않는다(ms) */
export const SWIPE_MAX_MS = 700;
/** 가로 이동이 세로 이동의 이 배수는 넘어야 한다 (비스듬한 스크롤과 구분) */
const SWIPE_RATIO = 1.8;
/** 따라오는 거리: 손가락 이동의 이만큼, 최대 FOLLOW_MAX px */
const FOLLOW = 0.22;
const FOLLOW_MAX = 36;
/** 가로로 이만큼 움직이면 '가로로 넘기는 중'으로 정한다(px) */
const LOCK_PX = 12;
/** 브라우저(사파리 등)의 가장자리 뒤로 가기 제스처 자리 — 홈 화면 앱이 아니면 양 끝은 비켜 준다(px) */
const EDGE_PX = 20;
/** 손을 떼면 본문이 제자리로 돌아오는 시간 (styles.css .page.swiping.settle) */
const SETTLE_MS = 200;

/** 홈 화면에 추가한 앱(standalone)으로 열렸는지 — 그때는 브라우저 뒤로 가기 제스처가 없다 */
export function isStandalone(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean };
  return (
    nav.standalone === true ||
    (typeof window.matchMedia === "function" && window.matchMedia("(display-mode: standalone)").matches)
  );
}

export type SwipeDir = "left" | "right";

/** 손가락 이동 → 넘기기 방향 (순수 함수) */
export function swipeDirection(dx: number, dy: number, ms: number): SwipeDir | null {
  if (ms > SWIPE_MAX_MS || Math.abs(dx) < SWIPE_MIN_PX) return null;
  if (Math.abs(dx) < Math.abs(dy) * SWIPE_RATIO) return null;
  return dx < 0 ? "left" : "right";
}

const NO_SWIPE =
  'input, textarea, select, [contenteditable="true"], [data-no-swipe], .recharts-wrapper, .sheet';

/** 손가락 아래에 넘기기보다 먼저인 것(가로 스크롤 상자·입력칸·차트·시트)이 있는지 */
export function blocksSwipe(target: EventTarget | null, root: HTMLElement): boolean {
  for (let el = target instanceof Element ? target : null; el && el !== root; el = el.parentElement) {
    if (el.matches(NO_SWIPE)) return true;
    if (el instanceof HTMLElement && el.scrollWidth > el.clientWidth + 1) {
      const ox = getComputedStyle(el).overflowX;
      if (ox === "auto" || ox === "scroll") return true;
    }
  }
  return false;
}

/**
 * 스크롤 상자에 넘기기를 붙인다. `onSwipe` 가 true 를 돌려주면 처리한 것(이동함).
 * `canFollow(dir)`: 그 방향으로 넘길 곳이 있는지 (없으면 따라오지도 않는다). 돌려주는 함수로 뗀다
 */
export function attachSwipe(
  el: HTMLElement,
  {
    enabled,
    canGo,
    onSwipe,
    motion,
  }: {
    enabled: () => boolean;
    canGo: (dir: SwipeDir) => boolean;
    onSwipe: (dir: SwipeDir) => void;
    motion: () => boolean;
  },
): () => void {
  let x0 = 0;
  let y0 = 0;
  let t0 = 0;
  let tracking = false;
  let locked = false; // 가로로 넘기는 중으로 정함
  let page: HTMLElement | null = null;

  const follow = (px: number) => {
    const p = page;
    if (!p) return;
    if (px !== 0) {
      p.classList.remove("settle");
      p.style.setProperty("--swipe-x", `${Math.round(px)}px`);
      p.classList.add("swiping");
      return;
    }
    if (!p.classList.contains("swiping")) return;
    // 손을 떼면 부드럽게 제자리로, 끝나면 transform 을 아예 뗀다 (fixed 요소의 기준이 바뀌지 않게)
    p.classList.add("settle");
    p.style.setProperty("--swipe-x", "0px");
    window.setTimeout(() => {
      p.classList.remove("swiping", "settle");
      p.style.removeProperty("--swipe-x");
    }, SETTLE_MS);
  };
  const end = () => {
    follow(0);
    tracking = false;
    locked = false;
    page = null;
  };

  const onStart = (e: TouchEvent) => {
    tracking = false;
    if (!enabled() || e.touches.length !== 1 || blocksSwipe(e.target, el)) return;
    const x = e.touches[0].clientX;
    if (!isStandalone() && (x < EDGE_PX || x > window.innerWidth - EDGE_PX)) return;
    tracking = true;
    locked = false;
    x0 = x;
    y0 = e.touches[0].clientY;
    t0 = performance.now();
    page = el.querySelector<HTMLElement>(":scope .page");
  };
  const onMove = (e: TouchEvent) => {
    if (!tracking) return;
    const dx = e.touches[0].clientX - x0;
    const dy = e.touches[0].clientY - y0;
    if (!locked) {
      if (Math.abs(dy) > LOCK_PX && Math.abs(dy) > Math.abs(dx)) {
        tracking = false; // 세로 스크롤 — 끝까지 손대지 않는다
        return;
      }
      if (Math.abs(dx) < LOCK_PX) return;
      locked = true;
    }
    const dir: SwipeDir = dx < 0 ? "left" : "right";
    if (motion() && canGo(dir)) {
      follow(Math.sign(dx) * Math.min(FOLLOW_MAX, Math.abs(dx) * FOLLOW));
    }
  };
  const onEnd = (e: TouchEvent) => {
    if (!tracking) return;
    const t = e.changedTouches[0];
    const dir = swipeDirection(t.clientX - x0, t.clientY - y0, performance.now() - t0);
    end();
    if (dir && canGo(dir)) onSwipe(dir);
  };

  el.addEventListener("touchstart", onStart, { passive: true });
  el.addEventListener("touchmove", onMove, { passive: true });
  el.addEventListener("touchend", onEnd, { passive: true });
  el.addEventListener("touchcancel", end, { passive: true });
  return () => {
    el.removeEventListener("touchstart", onStart);
    el.removeEventListener("touchmove", onMove);
    el.removeEventListener("touchend", onEnd);
    el.removeEventListener("touchcancel", end);
  };
}

/** 이 앱 안에서 뒤로 갈 곳이 있는지 (React Router 가 history.state.idx 에 순번을 둔다) */
export function canGoBack(): boolean {
  const idx = (window.history.state as { idx?: number } | null)?.idx;
  return typeof idx === "number" && idx > 0;
}

/** 안드로이드에서 짧게 '틱' (지원하지 않으면 아무것도 하지 않는다) */
export function tick(): void {
  try {
    navigator.vibrate?.(8);
  } catch {
    // 막혀 있으면 그냥 넘어간다
  }
}
