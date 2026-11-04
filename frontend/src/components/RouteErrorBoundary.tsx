import { Component, type ReactNode } from "react";

/**
 * 화면 하나를 그리다(또는 화면 조각을 받다) 문제가 생기면 그 자리만 안내로 바꾼다 — 머리말·메뉴는 그대로.
 * 새 배포 뒤 옛 조각을 못 받은 경우는 lazyPage.ts 가 먼저 한 번 새로고침하고, 그래도 안 되면 여기로 온다.
 * 화면을 옮기면(Layout 의 Page key) 새로 시작한다.
 */
export class RouteErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <section className="card route-error" role="alert">
        <h2>화면을 불러오지 못했어요</h2>
        <p className="muted">
          새 버전이 배포됐거나 연결이 잠시 끊겼을 수 있어요. 새로고침하면 대부분 해결돼요.
        </p>
        <button type="button" className="btn primary" onClick={() => window.location.reload()}>
          새로고침
        </button>
      </section>
    );
  }
}
