import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { registerServiceWorker } from "./push";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// 휴대폰 알림(웹 푸시)용 서비스 워커. 지원하지 않는 브라우저·HTTP·jsdom(테스트)에서는 아무것도 하지 않는다
registerServiceWorker();
