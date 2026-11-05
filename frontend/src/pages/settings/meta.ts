import type { SettingsSection } from "./lazy";

/** 주제가 어느 시장에 적용되는지: 공통 · 지금 고른 시장 · 안에서 시장을 고름 */
export type SectionScope = "common" | "market" | "both";

export interface SectionMeta {
  key: SettingsSection;
  icon: string;
  title: string;
  /** 목록에 늘 보이는 한 줄 설명 (지금 값은 화면이 따로 붙인다) */
  desc: string;
  scope: SectionScope;
  /** 빠른 이동(⌘K) 검색어 */
  keywords: string;
}

export const SECTION_META: readonly SectionMeta[] = [
  {
    key: "llm",
    icon: "🤖",
    title: "LLM 모델",
    desc: "무료/유료 · 유료 모델 · OpenRouter · 테스트용 LLM · 미장 매니저 LLM",
    scope: "common",
    keywords: "llm 모델 무료 유료 openrouter 테스트 엔드포인트 gpt",
  },
  {
    key: "alerts",
    icon: "🔔",
    title: "알림",
    desc: "휴대폰 알림 · 받을 알림 · 알림 받는 기기",
    scope: "common",
    keywords: "알림 푸시 push 휴대폰 기기",
  },
  {
    key: "schedule",
    icon: "⏰",
    title: "판단 일정·장외 거래",
    desc: "판단 세션 시간대 · 주기 · 데이·프리·애프터마켓에서도 판단·주문",
    scope: "both",
    keywords: "판단 주기 일정 스케줄 시간 장외 프리마켓 애프터마켓 데이마켓 시간외 nxt",
  },
  {
    key: "risk",
    icon: "🛡️",
    title: "리스크·주문",
    desc: "종목당 비중 · 주문 한도 · 손절·익절 · 신규 매수",
    scope: "market",
    keywords: "리스크 한도 손절 익절 주문 비중 현금",
  },
  {
    key: "manager",
    icon: "🦉",
    title: "매니저",
    desc: "이름 · 운용 방침 · 메모 카드 · 규칙 전략",
    scope: "market",
    keywords: "매니저 이름 이모지 정책 방침 전략 메모 페르소나",
  },
  {
    key: "account",
    icon: "🏦",
    title: "계좌·데이터",
    desc: "모의계좌 · 데이터 연결 상태 · 관심 종목 · LLM 요금",
    scope: "market",
    keywords: "계좌 모의 데이터 연결 토스 관심 종목 워치리스트 비용",
  },
];

export const metaOf = (key: SettingsSection): SectionMeta => SECTION_META.find((m) => m.key === key)!;
