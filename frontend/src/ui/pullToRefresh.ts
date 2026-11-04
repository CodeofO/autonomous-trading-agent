import type { PullState } from "./usePullToRefresh";

/*
 * 휴대폰 '당겨서 새로고침'의 손가락 따라가기 (휴대폰에서만 받는 조각 — 넓은 화면은 받지 않는다).
 * 당기는 거리는 스크롤 상자의 CSS 변수 --ptr 로 넘긴다 (styles.css .ptr)
 */

/** 이만큼(손가락 이동의 절반쯤) 당기면 놓았을 때 새로고침한다(px) */
export const PTR_THRESHOLD = 64;
/** 당겨지는 최대 거리(px) */
const PTR_MAX = 110;
/** 새로고침하는 동안 표시가 머무는 높이(px) */
const PTR_HOLD = 52;
/** 너무 빨리 끝나도 표시를 이만큼은 보여 준다 (깜빡이지 않게) */
const MIN_SPIN_MS = 450;

/** 손가락 아래쪽 요소 중 위로 스크롤할 것이 남은 것이 있으면(채팅 목록 등) 당겨서 새로고침하지 않는다 */
function innerScrolled(target: EventTarget | null, root: HTMLElement): boolean {
  for (let el = target instanceof Element ? target : null; el && el !== root; el = el.parentElement) {
    if (el instanceof HTMLElement && el.scrollTop > 0 && el.scrollHeight > el.clientHeight) return true;
  }
  return false;
}

/** 스크롤 상자에 손가락 처리를 붙인다. 돌려주는 함수로 뗀다 */
export function attachPullToRefresh(
  el: HTMLElement,
  { onState, refresh }: { onState: (s: PullState) => void; refresh: () => Promise<unknown> },
): () => void {
  let startY = 0;
  let startX = 0;
  let tracking = false;
  let pulling = false;
  let armed = false;
  let busy = false;

  const setPull = (px: number) => el.style.setProperty("--ptr", `${Math.round(px)}px`);
  const reset = () => {
    pulling = false;
    armed = false;
    el.classList.remove("ptr-on");
    setPull(0);
    onState("idle");
  };

  const onStart = (e: TouchEvent) => {
    tracking = false;
    if (busy || e.touches.length !== 1) return;
    if (el.scrollTop > 0 || innerScrolled(e.target, el)) return;
    tracking = true;
    startY = e.touches[0].clientY;
    startX = e.touches[0].clientX;
  };
  const onMove = (e: TouchEvent) => {
    if (!tracking || busy) return;
    const dy = e.touches[0].clientY - startY;
    const dx = e.touches[0].clientX - startX;
    if (!pulling) {
      // 아래로, 옆보다 세로로 더 움직였을 때만 시작 (가로 넘기기·위로 스크롤은 그대로)
      if (dy < 0 || Math.abs(dx) > Math.abs(dy)) {
        if (Math.abs(dy) > 6 || Math.abs(dx) > 6) tracking = false;
        return;
      }
      if (dy < 8 || el.scrollTop > 0) return;
      pulling = true;
      el.classList.add("ptr-on");
      onState("pulling");
    }
    if (e.cancelable) e.preventDefault(); // 브라우저 고무줄 대신 우리 표시를 끈다
    const dist = Math.min(PTR_MAX, (dy - 8) * 0.5);
    setPull(dist);
    if (dist >= PTR_THRESHOLD && !armed) {
      armed = true;
      onState("armed");
      navigator.vibrate?.(8); // 지원하는 휴대폰(안드로이드)에서는 '톡' 하는 진동
    } else if (dist < PTR_THRESHOLD && armed) {
      armed = false;
      onState("pulling");
    }
  };
  const onEnd = () => {
    if (!tracking) return;
    tracking = false;
    if (!pulling) return;
    if (!armed) {
      reset();
      return;
    }
    busy = true;
    setPull(PTR_HOLD);
    onState("refreshing");
    const started = performance.now();
    void Promise.resolve()
      .then(() => refresh())
      .catch(() => {})
      .then(() => new Promise((r) => setTimeout(r, Math.max(0, MIN_SPIN_MS - (performance.now() - started)))))
      .then(() => {
        busy = false;
        reset();
      });
  };

  el.dataset.ptr = "ready";
  el.addEventListener("touchstart", onStart, { passive: true });
  el.addEventListener("touchmove", onMove, { passive: false });
  el.addEventListener("touchend", onEnd, { passive: true });
  // 시스템이 손가락을 가져가면(전화·알림 등) 새로고침하지 않고 되돌린다
  const onCancel = () => {
    tracking = false;
    if (pulling && !busy) reset();
  };
  el.addEventListener("touchcancel", onCancel, { passive: true });
  return () => {
    el.removeEventListener("touchstart", onStart);
    el.removeEventListener("touchmove", onMove);
    el.removeEventListener("touchend", onEnd);
    el.removeEventListener("touchcancel", onCancel);
    el.classList.remove("ptr-on");
    el.style.removeProperty("--ptr");
    delete el.dataset.ptr;
  };
}
