/**
 * 메뉴 구성 (한 곳에서 정의 — 사이드바·휴대폰 탭바·더보기 시트가 같이 쓴다)
 *
 * - 넓은 화면 사이드바: 자주 보는 화면과 매니저·분석 화면, 두 묶음
 * - 설정은 오른쪽 위 ⚙️ (사이드바에 두지 않는다). LLM 비용은 설정 안에서 들어간다
 * - 휴대폰 하단 탭바: 4칸 + '더보기'(나머지 화면·설정·로그아웃을 시트로)
 */

export interface NavItem {
  to: string;
  label: string;
  /** 휴대폰 하단 탭바의 짧은 이름 */
  short: string;
  icon: string;
  /** 이 메뉴가 켜져 보이는 다른 주소 (예: 매니저 = 구조 + 메모 카드) */
  also?: string[];
  /** 더보기 시트의 설명 한 줄 */
  hint?: string;
}

export const NAV_GROUPS: { title: string; items: NavItem[] }[] = [
  {
    title: "매일 보기",
    items: [
      { to: "/", label: "홈", short: "홈", icon: "🏠" },
      { to: "/room", label: "트레이딩 룸", short: "채팅", icon: "💬" },
      { to: "/portfolio", label: "포트폴리오·주문", short: "자산", icon: "📒", also: ["/stock"] },
      { to: "/news", label: "뉴스 룸", short: "뉴스", icon: "📰" },
    ],
  },
  {
    title: "매니저·분석",
    items: [
      {
        to: "/agent",
        label: "매니저",
        short: "매니저",
        icon: "🦉",
        also: ["/memo"],
        hint: "에이전트 구조 · 기억 · 메모 카드",
      },
      {
        to: "/backtest",
        label: "백테스트",
        short: "백테스트",
        icon: "🧪",
        hint: "규칙 전략을 과거 시세로 시험",
      },
      { to: "/logs", label: "이벤트 로그", short: "로그", icon: "📜", hint: "주문·리스크·설정 변경 기록" },
    ],
  },
];

/** 설정 (오른쪽 위 ⚙️) — LLM 비용 화면도 설정에 속한다 */
export const SETTINGS: NavItem = {
  to: "/settings",
  label: "설정",
  short: "설정",
  icon: "⚙️",
  also: ["/costs"],
  hint: "LLM 모델 · 알림 · 판단 일정 · 리스크 · 매니저 · 계좌",
};

/** 휴대폰 하단 탭바 (마지막 칸은 '더보기') */
export const TAB_ITEMS: NavItem[] = NAV_GROUPS[0].items;
export const MORE = "#more";
export const TABS: string[] = [...TAB_ITEMS.map((n) => n.to), MORE];

/** 더보기 시트: 탭바에 없는 화면 + 설정 */
export const MORE_ITEMS: NavItem[] = [
  ...NAV_GROUPS[1].items,
  { to: "/costs", label: "LLM 비용", short: "비용", icon: "💸", hint: "지금 사용량으로 본 한 달 요금" },
  SETTINGS,
];

const under = (pathname: string, to: string) =>
  to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`);

/** 이 메뉴가 지금 주소에서 켜져 보이는지 */
export function isActive(item: NavItem, pathname: string): boolean {
  return [item.to, ...(item.also ?? [])].some((to) => under(pathname, to));
}

/** 휴대폰 하단 탭 위치 (탭바에 없는 화면은 모두 '더보기') */
export function tabIndex(pathname: string): number {
  const i = TAB_ITEMS.findIndex((n) => isActive(n, pathname));
  return i >= 0 ? i : TABS.length - 1;
}

/**
 * 가로 넘기기의 목적지 (휴대폰). 탭 화면(그 주소 그대로)은 옆 탭으로, 탭 아래 화면·더보기 화면은
 * 오른쪽으로 넘기면 뒤로(앱 안에 뒤로 갈 곳이 있을 때). 갈 곳이 없으면 null.
 */
export function swipeTarget(pathname: string, dir: "left" | "right", canBack: boolean): string | -1 | null {
  const i = TAB_ITEMS.findIndex((n) => n.to === pathname);
  if (i >= 0) {
    const j = dir === "left" ? i + 1 : i - 1;
    return j >= 0 && j < TAB_ITEMS.length ? TAB_ITEMS[j].to : null;
  }
  return dir === "right" && canBack ? -1 : null;
}

/** 내릴 때 상단 바를 접는 기준: 맨 위 근처는 항상 펴고, 이만큼 이상 내리면 접고, 이만큼 올리면 편다(px) */
const COMPACT_TOP = 56;
const COMPACT_STEP = 6;

/** 스크롤 위치 변화 → 상단 바를 접을지 (순수 함수, 작은 흔들림은 무시) */
export function topbarCompact(compact: boolean, lastY: number, y: number): boolean {
  if (y < COMPACT_TOP) return false;
  if (y - lastY > COMPACT_STEP) return true;
  if (lastY - y > COMPACT_STEP) return false;
  return compact;
}
