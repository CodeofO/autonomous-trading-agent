import { useEffect, useRef, useState } from "react";
import { useIsMobile } from "../mobile";

export type PullState = "idle" | "pulling" | "armed" | "refreshing";

/**
 * 휴대폰 '당겨서 새로고침' (본문 맨 위에서 아래로 당겼다 놓기). 상태(당기는 중·놓으면 새로고침·새로고침 중)를 돌려준다.
 * 손가락 처리(ui/pullToRefresh.ts)는 휴대폰에서 켜진 화면을 처음 열 때 받는다 — 넓은 화면은 받지 않는다
 */
export function usePullToRefresh(
  scroller: { readonly current: HTMLElement | null },
  enabled: boolean,
  onRefresh: () => Promise<unknown>,
): PullState {
  const [state, setState] = useState<PullState>("idle");
  const mobile = useIsMobile();
  const refresh = useRef(onRefresh);
  useEffect(() => {
    refresh.current = onRefresh;
  });

  useEffect(() => {
    const el = scroller.current;
    if (!el || !enabled || !mobile) return;
    let detach: (() => void) | null = null;
    let stopped = false;
    import("./pullToRefresh")
      .then((m) => {
        if (stopped) return;
        detach = m.attachPullToRefresh(el, { onState: setState, refresh: () => refresh.current() });
      })
      .catch(() => {}); // 못 받으면 당겨서 새로고침만 없다 (폴링·실시간 갱신은 그대로)
    return () => {
      stopped = true;
      detach?.();
      setState("idle");
    };
  }, [scroller, enabled, mobile]);

  return state;
}
