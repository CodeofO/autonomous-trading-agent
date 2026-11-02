import { useEffect } from "react";

// 휴대폰 화면(styles.css 의 @media (max-width: 760px) 와 같은 기준). 넓은 화면에서는 아래 동작을 하지 않는다
export const MOBILE_QUERY = "(max-width: 760px)";

const matches = (query: string) =>
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia(query).matches;

export const isMobile = () => matches(MOBILE_QUERY);
export const prefersReducedMotion = () => matches("(prefers-reduced-motion: reduce)");

/** 하단 탭 순서상 위치 (종목 상세는 '자산' 탭 아래로 본다). 탭이 아니면 -1 */
export function navIndex(pathname: string, tabs: readonly string[]): number {
  const path = pathname.startsWith("/stock/") ? "/portfolio" : pathname;
  return tabs.findIndex((to) => (to === "/" ? path === "/" : path === to || path.startsWith(`${to}/`)));
}

const TEXT_FIELD =
  'input:not([type="checkbox"]):not([type="radio"]):not([type="range"]), textarea, [contenteditable]';

const isTextField = (el: EventTarget | null) => el instanceof Element && el.matches(TEXT_FIELD);

/**
 * 휴대폰 화면 높이를 키보드에 맞춘다.
 * - `--vvh`: 실제로 보이는 높이(visualViewport). 키보드가 올라오면 앱 전체가 그만큼 줄어 입력창이 키보드 바로 위에 온다
 * - `kb-open`: 글자 입력 중에는 하단 탭바를 숨긴다(네이티브 앱처럼 키보드가 탭바를 대신한다)
 */
export function useMobileViewport() {
  useEffect(() => {
    const root = document.documentElement;
    const vv = window.visualViewport;
    const onResize = () => {
      if (!vv) return;
      root.style.setProperty("--vvh", `${Math.round(vv.height)}px`);
      // iOS 는 키보드를 띄우며 문서를 밀어 올린다 — 앱 높이를 줄였으니 제자리로
      if (isMobile() && root.classList.contains("kb-open") && window.scrollY) window.scrollTo(0, 0);
    };
    const onFocusIn = (e: FocusEvent) => {
      if (isMobile() && isTextField(e.target)) root.classList.add("kb-open");
    };
    const onFocusOut = (e: FocusEvent) => {
      if (!isTextField(e.relatedTarget)) root.classList.remove("kb-open");
    };
    // iOS Safari 는 touchstart 리스너가 있어야 :active(눌림) 스타일을 보여 준다
    const noop = () => {};
    onResize();
    vv?.addEventListener("resize", onResize);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("focusout", onFocusOut);
    document.addEventListener("touchstart", noop, { passive: true });
    return () => {
      vv?.removeEventListener("resize", onResize);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("focusout", onFocusOut);
      document.removeEventListener("touchstart", noop);
      root.classList.remove("kb-open");
      root.style.removeProperty("--vvh");
    };
  }, []);
}
