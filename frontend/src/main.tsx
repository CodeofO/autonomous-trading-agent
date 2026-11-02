import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

function App() {
  return <h1>Toss Paper Trader — 모의투자 (PAPER)</h1>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
