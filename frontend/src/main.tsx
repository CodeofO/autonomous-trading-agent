import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { reloadForNewBuild } from "./lazyPage";
import { parseView, readStore, VIEW_KEY, viewFromUrl } from "./market";
import { registerServiceWorker } from "./push";
import { preloadPage } from "./routes";
import { startVersionWatch } from "./version";
import "./styles.css";

// 지금 주소의 화면 조각을 바로 받기 시작한다 (React 가 그리기 시작할 때까지 기다리지 않는다)
preloadPage(
  window.location.pathname,
  viewFromUrl(window.location.search) ?? parseView(readStore(VIEW_KEY)) ?? "ALL",
);
// '전체' 홈은 시장 전환에서 가장 자주 가는 화면이라 작은 조각(~4KB gzip)을 처음부터 받아 둔다.
// (측정: 미장→전체 첫 전환이 화면 조각을 그때 받느라 180→349ms 로 느려졌었다 — docs/perf/REFACTOR_REPORT.md)
preloadPage("/", "ALL");

// 새 배포 뒤 옛 화면이 지난 배포의 조각(CSS·JS)을 미리 받다 실패하면 한 번 새로고침해 새 화면을 받는다
window.addEventListener("vite:preloadError", (event) => {
  if (reloadForNewBuild()) event.preventDefault();
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// 재배포(화면·서버)를 알아채면 열려 있는 화면을 스스로 새로고침한다 (글 쓰는 중·창이 열려 있으면 기다림)
startVersionWatch();

// 휴대폰 알림(웹 푸시)용 서비스 워커. 지원하지 않는 브라우저·HTTP·jsdom(테스트)에서는 아무것도 하지 않는다.
// 서비스 워커는 화면 파일을 캐시하지 않는다(public/sw.js) — 새 배포의 조각 이름이 바뀌어도 옛 캐시가 남지 않는다
registerServiceWorker();
