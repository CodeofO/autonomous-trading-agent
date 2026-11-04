import type { NewsEdition, NewsImpact, NewsRoom, NewsSection } from "../api/types";
import { fmtHm } from "./format";

/** 뉴스 룸 캐시 키 — 시장과 관계없는 공용 화면이라 시장을 붙이지 않는다 */
export const NEWS_ROOM_KEY = ["news-room"];
/** 모으는 동안 이 간격으로 다시 받는다(ms) */
export const NEWS_POLL_MS = 1500;
/** 평소에는 이 간격으로 다시 받는다(ms) — 07·12·17시 자동 갱신을 화면에 반영 */
export const NEWS_IDLE_MS = 5 * 60_000;

/** 정기 갱신이 슬롯보다 이만큼(분) 넘게 늦으면 '늦게 모음' (서버가 꺼져 있다 켜지며 따라잡은 판) */
const LATE_MIN = 30;
const minutes = (hm: string) => {
  const [h, m] = hm.split(":").map(Number);
  return h * 60 + m;
};

/** 판 이름: "07:00 판" · "17:00 판 · 늦게 모음" · "수동 갱신" */
export function editionLabel(e: NewsEdition): string {
  if (e.trigger !== "schedule" || !e.slot) return "수동 갱신";
  const late = (minutes(fmtHm(e.created_at)) - minutes(e.slot) + 1440) % 1440;
  return late > LATE_MIN ? `${e.slot} 판 · 늦게 모음` : `${e.slot} 판`;
}

/** 마지막 시도가 지금 보이는 판보다 나중에 실패했으면 그 이유 (이전 판이 그대로 보이고 있음을 알린다) */
export function staleError(room: NewsRoom): string | null {
  if (!room.last_error) return null;
  if (!room.updated_at || !room.last_attempt) return room.last_error;
  return Date.parse(room.last_attempt) >= Date.parse(room.updated_at) ? room.last_error : null;
}

/** 요약 줄 안내 ("AI 요약 · gpt-5-mini" · "AI 요약 꺼짐" · "LLM 없음 — 기사만") */
export function summaryNote(room: NewsRoom): string {
  if (!room.settings.summary) return "AI 요약 꺼짐 — 기사 목록만";
  if (!room.llm.configured) return "LLM 이 설정되지 않아 기사 목록만";
  return `AI 요약 · ${room.llm.model ?? "활성 LLM"}`;
}

/** 링크는 http(s) 만 연다 (그 밖의 주소는 글자만) */
export const safeLink = (url: string) => (/^https?:\/\//i.test(url) ? url : null);

/** 증시 영향 띠를 접었는지 기억하는 저장소 키 ("0" = 접음) */
export const IMPACT_OPEN_KEY = "newsroom.impact.open";

/** 증시 영향 카드로 보일 섹션 (예전 서버처럼 impact 가 없으면 뺀다) */
export function impactSections(sections: NewsSection[]): { section: NewsSection; impact: NewsImpact }[] {
  return sections.flatMap((section) => (section.impact ? [{ section, impact: section.impact }] : []));
}
