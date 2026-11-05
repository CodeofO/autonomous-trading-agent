import { Suspense, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { Link, Navigate, useLocation, useMatch, useNavigate } from "react-router-dom";
import { SkeletonCards } from "../components/Skeleton";
import { useSystem } from "../hooks";
import { MARKET_META, useMarket } from "../market";
import { useIsMobile } from "../mobile";
import { intervalLabel } from "../utils/schedule";
import { llmChipText } from "../utils/llm";
import {
  DEFAULT_SECTION,
  isSettingsSection,
  SECTION_PAGES,
  SETTINGS_SECTIONS,
  type SettingsSection,
} from "./settings/lazy";
import { metaOf, SECTION_META, type SectionMeta } from "./settings/meta";
import "./settings/settings.css";

/** 목록 → 주제 화면으로 들어왔는지 (뒤로 가기를 브라우저 뒤로 가기로 할 수 있는지) */
interface FromHub {
  fromHub?: boolean;
}

/**
 * ⚙️ 설정 — 주제별로 나눈 설정 화면.
 *
 * - 넓은 화면: 왼쪽 주제 목록 + 오른쪽 내용. /settings 는 LLM 모델을 먼저 보여 준다 (예전 설정 화면 맨 위 카드)
 * - 휴대폰: /settings 는 목록(지금 값 한 줄씩), 누르면 /settings/<주제> 로 밀려 들어가고 '‹ 설정'으로 돌아온다
 * - 주제마다 따로 받는 조각(settings/lazy.ts). 시장마다 다른 주제는 어느 시장에 적용되는지 위에 보여 준다
 */
export function Settings() {
  const match = useMatch("/settings/:section");
  const raw = match?.params.section;
  const mobile = useIsMobile();
  // 주제 화면에서 목록으로 돌아왔는지 (휴대폰에서 목록이 왼쪽에서 밀려 나온다)
  const prev = useRef<string | undefined>(raw);
  const cameBack = prev.current !== undefined && raw === undefined;
  useEffect(() => {
    prev.current = raw;
  }, [raw]);
  // 손이 닿기 전에 나머지 주제 조각도 쉬는 동안 받아 둔다 (목록에서 누르면 바로 열리게)
  useEffect(() => {
    const run = () => SETTINGS_SECTIONS.forEach((k) => void SECTION_PAGES[k].preload().catch(() => {}));
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(run, { timeout: 4000 });
      return () => window.cancelIdleCallback(id);
    }
    const t = window.setTimeout(run, 1500);
    return () => window.clearTimeout(t);
  }, []);

  if (raw !== undefined && !isSettingsSection(raw)) return <Navigate to="/settings" replace />;
  const section: SettingsSection | null = isSettingsSection(raw) ? raw : null;
  const shown = section ?? (mobile ? null : DEFAULT_SECTION);

  return (
    <div className={`settings ${section ? "in-section" : "at-hub"}`} data-testid="settings">
      {(!mobile || !section) && <SettingsNav current={shown} pop={mobile && cameBack} />}
      {shown && <SettingsPane key={shown} section={shown} mobile={mobile} />}
    </div>
  );
}

/** 주제 목록 (휴대폰은 iOS 설정처럼 묶음 카드, 넓은 화면은 왼쪽에 붙어 있는 목록) */
function SettingsNav({ current, pop }: { current: SettingsSection | null; pop: boolean }) {
  const [anim] = useState(pop); // 처음 그릴 때 한 번만 — 다시 그려져도 애니메이션이 다시 돌지 않게
  const { market, available } = useMarket();
  const many = available.length > 1;
  const meta = MARKET_META[market];
  const summary = useSummaries();
  const common = SECTION_META.filter((m) => m.scope === "common");
  const perMarket = SECTION_META.filter((m) => m.scope !== "common");
  return (
    <nav className={`settings-nav ${anim ? "pop" : ""}`} aria-label="설정 주제" data-testid="settings-hub">
      <NavGroup title="앱 공통">
        {common.map((m) => (
          <NavItem key={m.key} m={m} active={current === m.key} sub={summary[m.key]} />
        ))}
        {/* LLM 요금은 설정에 속한 화면 (사이드바에서 뺐다) */}
        <Link to="/costs" className="settings-item press">
          <span className="settings-icon" aria-hidden>
            💸
          </span>
          <span className="settings-text">
            <b>LLM 요금</b>
            <small className="muted">모델·토큰별 실제 요금 · 무료/유료·계좌별로 나눠 보기</small>
          </span>
          <span className="settings-chev" aria-hidden>
            ›
          </span>
        </Link>
      </NavGroup>
      <NavGroup
        title={many ? `${meta.flag} ${meta.name} 매니저` : "매니저·계좌"}
        note={many ? "위 전환에서 고른 시장에만 적용돼요" : undefined}
      >
        {perMarket.map((m) => (
          <NavItem key={m.key} m={m} active={current === m.key} sub={summary[m.key]} />
        ))}
      </NavGroup>
    </nav>
  );
}

function NavGroup({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return (
    <section className="settings-group" aria-label={title}>
      <h3 className="settings-group-title">
        {title}
        {note && <small className="muted"> · {note}</small>}
      </h3>
      <div className="settings-group-items">{children}</div>
    </section>
  );
}

function NavItem({ m, active, sub }: { m: SectionMeta; active: boolean; sub?: string }) {
  const warm = () => void SECTION_PAGES[m.key].preload().catch(() => {});
  return (
    <Link
      to={`/settings/${m.key}`}
      state={{ fromHub: true } satisfies FromHub}
      className={`settings-item press ${active ? "active" : ""}`}
      aria-current={active ? "page" : undefined}
      onPointerEnter={warm}
      onFocus={warm}
      onTouchStart={warm}
      data-section={m.key}
    >
      <span className="settings-icon" aria-hidden>
        {m.icon}
      </span>
      <span className="settings-text">
        <b>{m.title}</b>
        <small className="muted">{sub ?? m.desc}</small>
      </span>
      <span className="settings-chev" aria-hidden>
        ›
      </span>
    </Link>
  );
}

/** 목록에 붙이는 지금 값 한 줄 (상단 바가 이미 받아 둔 시장 상태로 — 요청을 더 하지 않는다) */
function useSummaries(): Partial<Record<SettingsSection, string>> {
  const { market } = useMarket();
  const { data: kr } = useSystem("KR");
  const { data: sys } = useSystem();
  const meta = MARKET_META[market];
  const out: Partial<Record<SettingsSection, string>> = {};
  if (kr) out.llm = `${llmChipText(kr.llm)} · 무료/유료 · 테스트용 LLM`;
  if (sys) {
    if (sys.agent_interval_minutes > 0)
      out.schedule = `${meta.flag} 장중 ${intervalLabel(sys.agent_interval_minutes)}마다 판단 · 시간대`;
    out.manager = `${sys.persona.emoji} ${sys.persona.name} · 운용 방침 · 메모 카드 · 규칙 전략`;
    out.account = `${sys.toss_ok ? "토스 실시세 연결됨" : "⚠️ 시세 연결 안 됨"} · 모의계좌 · 관심 종목`;
  }
  return out;
}

/** 주제가 어느 시장에 적용되는지 (시장이 하나뿐이면 보이지 않는다) */
function ScopeChip({ m }: { m: SectionMeta }) {
  const { market, available } = useMarket();
  if (available.length < 2) return null;
  const meta = MARKET_META[market];
  const text =
    m.scope === "market"
      ? `${meta.flag} ${meta.name}에만 적용돼요 — 위 전환에서 시장을 바꿀 수 있어요`
      : m.scope === "both"
        ? "🇰🇷 국장 · 🇺🇸 미장 따로 — 아래 카드에서 시장을 골라요"
        : m.key === "llm"
          ? "국장·공통 기준 — 미장 매니저 LLM 은 카드 아래에서 따로"
          : "모든 시장 공통";
  return (
    <p className={`settings-scope scope-${m.scope}`} data-testid="settings-scope">
      {text}
    </p>
  );
}

/** 오른쪽(휴대폰은 전체) 주제 화면 */
function SettingsPane({ section, mobile }: { section: SettingsSection; mobile: boolean }) {
  const m = metaOf(section);
  const Page = SECTION_PAGES[section];
  const navigate = useNavigate();
  const location = useLocation();
  const pane = useRef<HTMLDivElement>(null);
  const fromHub = !!(location.state as FromHub | null)?.fromHub;
  // 목록에서 눌러 들어왔을 때만 오른쪽에서 밀려 들어온다 (주소로 바로 열면 평소 화면 등장 효과만)
  const [push] = useState(mobile && fromHub);
  const back = () => {
    if (fromHub) navigate(-1);
    else navigate("/settings");
  };
  // 넓은 화면에서 다른 주제를 고르면 내용 맨 위부터 (목록은 왼쪽에 붙어 있다)
  useLayoutEffect(() => {
    if (mobile) return;
    const scroller = pane.current?.closest(".content");
    const top = pane.current?.offsetTop ?? 0;
    if (scroller && scroller.scrollTop > top) scroller.scrollTop = 0;
  }, [mobile]);

  return (
    <div className={`settings-pane ${push ? "push" : ""}`} ref={pane} data-testid="settings-pane">
      <header className="settings-head">
        {mobile && (
          <button type="button" className="settings-back" onClick={back} aria-label="설정 목록으로">
            <span aria-hidden>‹</span> 설정
          </button>
        )}
        <h2 className="settings-title">
          <span aria-hidden>{m.icon}</span> {m.title}
        </h2>
      </header>
      <ScopeChip m={m} />
      <Suspense fallback={<SkeletonCards heights={[360, 200]} label={`${m.title} 불러오는 중`} />}>
        <Page />
      </Suspense>
    </div>
  );
}
