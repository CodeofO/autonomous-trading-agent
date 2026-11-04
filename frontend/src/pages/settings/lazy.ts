import { lazyWithPreload, type Preloadable } from "../../lazyPage";

/*
 * 설정 화면의 주제(섹션)마다 따로 받는 조각. 주소는 /settings/<주제> (/settings 는 목록).
 * 이름·설명(meta.ts)은 설정 화면 조각에, 여기는 받는 방법만 — 메뉴에 손이 닿을 때 미리 받는 데 쓴다 (routes.ts)
 */
export type SettingsSection = "llm" | "alerts" | "schedule" | "risk" | "manager" | "account";

export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  "llm",
  "alerts",
  "schedule",
  "risk",
  "manager",
  "account",
];

export const isSettingsSection = (v: unknown): v is SettingsSection =>
  typeof v === "string" && (SETTINGS_SECTIONS as readonly string[]).includes(v);

/** 넓은 화면에서 /settings 를 열면 오른쪽에 먼저 보여 주는 주제 (예전 설정 화면 맨 위 카드) */
export const DEFAULT_SECTION: SettingsSection = "llm";

export const SECTION_PAGES: Record<SettingsSection, Preloadable> = {
  llm: lazyWithPreload(
    () => import("./LlmSection"),
    (m) => m.LlmSection,
  ),
  alerts: lazyWithPreload(
    () => import("./AlertsSection"),
    (m) => m.AlertsSection,
  ),
  schedule: lazyWithPreload(
    () => import("./ScheduleSection"),
    (m) => m.ScheduleSection,
  ),
  risk: lazyWithPreload(
    () => import("./RiskSection"),
    (m) => m.RiskSection,
  ),
  manager: lazyWithPreload(
    () => import("./ManagerSection"),
    (m) => m.ManagerSection,
  ),
  account: lazyWithPreload(
    () => import("./AccountSection"),
    (m) => m.AccountSection,
  ),
};

/** /settings/risk → "risk" (설정 화면이 아니거나 모르는 주제면 null) */
export function sectionOf(pathname: string): SettingsSection | null {
  const m = /^\/settings\/([^/?#]+)/.exec(pathname);
  return m && isSettingsSection(m[1]) ? m[1] : null;
}
