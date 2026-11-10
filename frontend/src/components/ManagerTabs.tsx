import { Link, useLocation } from "react-router-dom";

const TABS = [
  { to: "/agent", label: "구조·기억" },
  { to: "/memo", label: "메모 카드" },
];

/** '매니저' 메뉴 안의 두 화면(에이전트 구조 · 메모 카드)을 오가는 탭 — 메뉴에는 '매니저' 하나만 둔다 */
export function ManagerTabs() {
  const { pathname } = useLocation();
  return (
    <nav className="seg mgr-tabs" aria-label="매니저 화면">
      {TABS.map((t) => {
        const on = pathname === t.to || pathname.startsWith(`${t.to}/`);
        return (
          <Link
            key={t.to}
            to={t.to}
            className={on ? "active" : undefined}
            aria-current={on ? "page" : undefined}
            replace
          >
            {t.label}
          </Link>
        );
      })}
    </nav>
  );
}
