/**
 * 선 아이콘 (24×24, stroke) — 메뉴·상단 바·버튼의 이모지를 대신한다.
 * 이름이 목록에 없으면 받은 글자를 그대로 그린다(캐릭터 이모지 등 아직 글자로 두는 곳).
 */
const PATHS = {
  home: "M3 11l9-8 9 8M5 10v10h14V10M10 20v-6h4v6",
  chat: "M4 5h16v11H9l-5 4z",
  wallet: "M3 7h16a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 7V6a2 2 0 0 1 2-2h11v3M16 13.5h2",
  news: "M4 5h13v14H6a2 2 0 0 1-2-2zM17 9h3v8a2 2 0 0 1-2 2M8 9h5M8 13h5M8 16h3",
  bot: "M12 3v3M6 6h12a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2zM9 11v2M15 11v2M9 16h6",
  flask: "M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3M7.5 15h9",
  clock: "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM12 7v5l3 2",
  history: "M3 12a9 9 0 1 0 3-6.7M3 4v5h5M12 8v4l3 2",
  list: "M8 6h12M8 12h12M8 18h12M4 6h.01M4 12h.01M4 18h.01",
  settings: "M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M14 4v4M8 10v4M16 16v4",
  coins:
    "M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18zM14.5 9.5c-.5-1-1.4-1.5-2.5-1.5-1.5 0-2.5.8-2.5 2s1 1.7 2.5 2 2.5.8 2.5 2-1 2-2.5 2c-1.1 0-2-.5-2.5-1.5M12 6v2M12 16v2",
  search: "M11 4a7 7 0 1 0 0 14a7 7 0 0 0 0-14zM20 20l-4-4",
  power: "M12 3v9M6.3 6.3a8 8 0 1 0 11.4 0",
  more: "M4 7h16M4 12h16M4 17h16",
  refresh: "M20 12a8 8 0 1 1-2.6-5.9M20 4v5h-5",
  play: "M7 4l13 8-13 8z",
  chart: "M4 20V4M4 20h16M8 16l4-5 3 3 5-7",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01",
} as const;

export type IconName = keyof typeof PATHS;

export const isIconName = (v: string): v is IconName => v in PATHS;

export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  if (!isIconName(name)) return <>{name}</>;
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
    >
      <path d={PATHS[name]} />
    </svg>
  );
}
