import { createElement, lazy, useState, type ComponentType } from "react";

/**
 * 따로 받는 조각(코드 분할)으로 나눈 화면·차트 (React.lazy + 미리 받기).
 *
 * - preload(): 미리 받아 둔다 (메뉴에 손가락·마우스가 올라갔을 때, 첫 화면이 다 뜬 뒤 쉬는 동안).
 *   같은 약속(Promise)을 다시 쓰므로 여러 번 불러도 한 번만 받는다
 * - 새로 배포된 뒤 열려 있던 옛 화면이 지난 배포의 조각을 찾으면(서버에 더는 없음 → 404) 한 번만 새로고침한다.
 *   그래도 못 받으면 오류를 그대로 던져 RouteErrorBoundary 가 새로고침 버튼을 보여 준다
 */
export type Preloadable<P extends object = object> = ComponentType<P> & {
  preload: () => Promise<unknown>;
};

const RELOAD_KEY = "chunk-reload-at";
// 이 시간 안에 이미 한 번 새로고침했으면 다시 하지 않는다 (서버 쪽 문제로 계속 실패할 때 무한 새로고침 방지)
const RELOAD_GUARD_MS = 30_000;

/** 지난 배포의 조각을 못 받았을 때: 처음이면 새로고침하고 true, 방금 새로고침했는데 또 실패면 false */
export function reloadForNewBuild(): boolean {
  let last = 0;
  try {
    last = Number(window.sessionStorage.getItem(RELOAD_KEY)) || 0;
  } catch {
    /* 저장소 사용 불가 — 아래에서 기록을 못 남기므로 새로고침하지 않는다 */
  }
  if (Date.now() - last < RELOAD_GUARD_MS) return false;
  try {
    window.sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  } catch {
    return false; // 기록을 못 남기면 새로고침이 반복될 수 있어 오류 화면으로
  }
  window.location.reload();
  return true;
}

export function lazyWithPreload<M, P extends object = object>(
  load: () => Promise<M>,
  pick: (m: M) => ComponentType<P>,
): Preloadable<P> {
  let loading: Promise<M> | undefined;
  let loaded: ComponentType<P> | undefined; // 이미 받은 화면 — React.lazy 를 거치지 않고 바로 그린다
  const preload = () => {
    loading ??= load().then(
      (m) => {
        loaded = pick(m);
        return m;
      },
      (e: unknown) => {
        loading = undefined; // 다음에 다시 시도할 수 있게
        throw e;
      },
    );
    return loading;
  };
  const Lazy = lazy(() =>
    preload().then(
      (m) => ({ default: pick(m) }),
      (e: unknown) => {
        // 새로고침하는 동안은 자리 표시를 그대로 둔다
        if (reloadForNewBuild()) return new Promise<never>(() => {});
        throw e;
      },
    ),
  );
  /**
   * 미리 받아 둔 화면은 React.lazy 가 첫 렌더에서 한 번 멈추는(Suspense) 일 없이 바로 그린다.
   * 측정: '전체' 전환이 시장 전환 애니메이션 안에서 한 번 멈춰 내용이 애니메이션 뒤(180→349ms)에 들어갔었다.
   * 어느 쪽으로 그릴지는 처음 그릴 때 한 번만 정한다 — 떠 있는 동안 바뀌면 화면이 통째로 다시 만들어져
   * 입력·스크롤 상태가 사라진다.
   */
  function Page(props: P) {
    const [Direct] = useState(() => loaded);
    return Direct ? createElement(Direct, props) : createElement(Lazy, props);
  }
  return Object.assign(Page, { preload });
}
