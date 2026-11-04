import type { CSSProperties } from "react";
import { flushSync } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";
import type { MarketView } from "../api/types";
import { isAllRoute, useMarket } from "../market";
import { prefersReducedMotion } from "../mobile";
import { preloadPage } from "../routes";

const ITEMS: { v: MarketView; label: string }[] = [
  { v: "ALL", label: "전체" },
  { v: "KR", label: "국장" },
  { v: "US", label: "미장" },
];

/**
 * 화면 맨 위 '전체 · 국장 · 미장' 전환. 시장마다 장중(●)/마감(○) 점, 미장은 늘 모의투자라 '모의' 표시.
 * 시장이 하나뿐이면(미장 꺼짐) 그리지 않는다.
 */
export function MarketSwitch() {
  const { view, available, markets, setView } = useMarket();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  if (available.length < 2) return null;

  const items = ITEMS.filter((i) => i.v === "ALL" || available.includes(i.v));
  const index = Math.max(
    0,
    items.findIndex((i) => i.v === view),
  );

  const pick = (v: MarketView) => {
    // '전체'는 한눈에 보기 화면 — 시장 화면에서 누르면 그리로 간다
    const toHome = v === "ALL" && !isAllRoute(pathname);
    if (toHome) {
      setView(v);
      navigate("/");
      return;
    }
    if (v === view || prefersReducedMotion() || typeof document.startViewTransition !== "function") {
      setView(v);
      return;
    }
    // 본문은 짧게 겹쳐 바뀌고, 선택 표시는 새 자리로 미끄러진다 (styles.css .vt-mkt)
    const root = document.documentElement;
    root.classList.add("vt-mkt");
    const t = document.startViewTransition(() => flushSync(() => setView(v)));
    const done = () => root.classList.remove("vt-mkt");
    t.ready.catch(() => {});
    t.finished.then(done, done);
  };

  return (
    <div
      className="mkt-switch"
      role="group"
      aria-label="시장 보기"
      data-testid="market-switch"
      style={{ "--seg-i": index, "--seg-n": items.length } as CSSProperties}
    >
      <span className="mkt-ind" aria-hidden />
      {items.map((i) => {
        const info = i.v === "ALL" ? undefined : markets?.find((m) => m.code === i.v);
        const state = info ? (info.open_now ? "장중" : "장 마감") : null;
        const on = view === i.v;
        const label = [i.label, state, i.v === "US" ? "모의투자" : null].filter(Boolean).join(" · ");
        return (
          <button
            key={i.v}
            type="button"
            className={on ? "active" : undefined}
            aria-pressed={on}
            aria-label={label}
            title={label}
            onClick={() => pick(i.v)}
            // 홈('/')은 보기에 따라 한눈에 보기·대시보드로 화면이 바뀐다 — 누르기 전에 그 화면 조각을 받아 둔다
            onPointerEnter={() => preloadPage(i.v === "ALL" ? "/" : pathname, i.v)}
            onFocus={() => preloadPage(i.v === "ALL" ? "/" : pathname, i.v)}
          >
            {info && <span className={`mkt-dot ${info.open_now ? "open" : ""}`} aria-hidden />}
            <span className="mkt-name">{i.label}</span>
            {i.v === "US" && (
              <span className="mkt-paper" aria-hidden>
                모의
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
