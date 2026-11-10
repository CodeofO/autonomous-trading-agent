import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { NewsEdition, NewsRoom } from "../api/types";
import { CommandPalette } from "../components/CommandPalette";
import { isAllRoute } from "../market";
import { NewsRoomPage } from "../pages/NewsRoom";
import { pageFor } from "../routes";
import { clearToasts } from "../ui/toast";
import {
  editionLabel,
  IMPACT_OPEN_KEY,
  impactSections,
  safeLink,
  staleError,
  summaryNote,
} from "../utils/newsroom";
import { apiError, mockFetch, renderWithProviders } from "./utils";

// 뉴스 룸 (D-56): 섹션별 뉴스·갱신 시각·다음 갱신 시각, 지금 새로 모으기, AI 요약 켜기/끄기, ⌘K
// 증시 영향 3줄 (D-59): 섹션별 카드, 'AI 요약 아님', 접기·펼치기(기억), 휴대폰 가로 넘기기 상자

/** 테스트마다 빈 저장소 (접기 상태가 다음 테스트로 넘어가지 않게) */
function memoryStorage(): Storage {
  const m = new Map<string, string>();
  return {
    get length() {
      return m.size;
    },
    clear: () => m.clear(),
    getItem: (k) => m.get(k) ?? null,
    key: (i) => [...m.keys()][i] ?? null,
    removeItem: (k) => void m.delete(k),
    setItem: (k, v) => void m.set(k, String(v)),
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-11-05T07:30:00+09:00"));
  vi.stubGlobal("localStorage", memoryStorage());
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  clearToasts();
});

const SECTIONS = [
  { key: "world", title: "세계·국제", icon: "🌍" },
  { key: "global_econ", title: "미국·글로벌 경제", icon: "🌐" },
  { key: "kr_market", title: "국내 경제·증시", icon: "📈" },
  { key: "kr_issues", title: "국내 주요 이슈", icon: "🇰🇷" },
];

const EDITION: NewsEdition = {
  id: 3,
  created_at: "2026-11-05T07:00:04+09:00",
  trigger: "schedule",
  slot: "07:00",
  headline: "미중 정상회담이 오늘 시장의 중심",
  summary_model: "news-model",
  summary_error: null,
  stats: { queries: 14, failed_queries: 0, articles: 3 },
  sections: [
    {
      ...SECTIONS[0],
      summary: "미중 정상이 만나 관세를 논의했다.",
      errors: [],
      impact: {
        source: "ai",
        lines: [
          {
            text: "관세 협상 진전 — 수출주 관심",
            link: "https://a.example.com/1",
            title: "미중 정상회담 개최…관세 협상 진전",
          },
          { text: "근거 기사를 모르는 줄", link: null, title: null },
          {
            text: "이상한 주소는 링크로 만들지 않음",
            link: "javascript:alert(1)",
            title: "이상한 주소 기사",
          },
        ],
      },
      items: [
        {
          title: "미중 정상회담 개최…관세 협상 진전",
          description: "양국 정상이 만나",
          link: "https://a.example.com/1",
          domain: "a.example.com",
          published_at: "2026-11-05T06:30:00+09:00",
          related: 3,
        },
        {
          title: "이상한 주소 기사",
          description: "",
          link: "javascript:alert(1)",
          domain: "",
          published_at: null,
          related: 1,
        },
      ],
    },
    { ...SECTIONS[1], summary: null, errors: [], items: [], impact: { source: "rule", lines: [] } },
    {
      ...SECTIONS[2],
      summary: null,
      errors: ["'코스닥' 검색 실패"],
      impact: {
        source: "rule",
        lines: [
          {
            text: "코스피 외국인 매수에 상승 출발",
            link: "https://e.example.com/5",
            title: "코스피 외국인 매수에 상승 출발",
          },
        ],
      },
      items: [
        {
          title: "코스피 외국인 매수에 상승 출발",
          description: "",
          link: "https://e.example.com/5",
          domain: "e.example.com",
          published_at: "2026-11-05T06:00:00+09:00",
          related: 1,
        },
      ],
    },
    { ...SECTIONS[3], summary: null, errors: [], items: [] },
  ],
};

const room = (over: Partial<NewsRoom> = {}): NewsRoom => ({
  edition: EDITION,
  updated_at: EDITION.created_at,
  next_update_at: "2026-11-05T12:00:00+09:00",
  schedule: ["07:00", "12:00", "17:00"],
  timezone: "Asia/Seoul",
  auto: true,
  available: true,
  reason: null,
  refreshing: false,
  last_error: null,
  last_attempt: EDITION.created_at,
  settings: { summary: true },
  llm: { configured: true, model: "news-model" },
  sections: SECTIONS,
  ...over,
});

describe("뉴스 룸 화면", () => {
  it("섹션별 뉴스, 갱신 시각·다음 갱신 시각, 오늘의 흐름, 관련 기사 수를 보여 준다", async () => {
    mockFetch({ "/api/news-room": room() });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    expect(screen.getByRole("status", { name: "뉴스 룸을 불러오는 중" })).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "뉴스 룸" })).toBeInTheDocument();
    expect(screen.getByTestId("news-updated")).toHaveTextContent("오늘 07:00");
    expect(screen.getByTestId("news-updated")).toHaveTextContent("30분 전");
    expect(screen.getByTestId("news-updated")).toHaveTextContent("07:00 판");
    expect(screen.getByTestId("news-next")).toHaveTextContent("오늘 12:00");
    expect(screen.getByText(/매일 07:00 · 12:00 · 17:00 \(한국 시각\) 자동 갱신/)).toBeInTheDocument();
    expect(screen.getByTestId("news-headline")).toHaveTextContent("미중 정상회담이 오늘 시장의 중심");

    const world = screen.getByRole("region", { name: "세계·국제" });
    expect(within(world).getByText("미중 정상이 만나 관세를 논의했다.")).toBeInTheDocument();
    const link = within(world).getByRole("link", { name: "미중 정상회담 개최…관세 협상 진전" });
    expect(link).toHaveAttribute("href", "https://a.example.com/1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(world).getByText("관련 3")).toBeInTheDocument();
    expect(within(world).getByText("a.example.com")).toBeInTheDocument();
    // http(s) 가 아닌 주소는 링크로 만들지 않는다
    expect(within(world).queryByRole("link", { name: "이상한 주소 기사" })).not.toBeInTheDocument();
    expect(within(world).getByText("이상한 주소 기사")).toBeInTheDocument();

    const kr = screen.getByRole("region", { name: "국내 경제·증시" });
    expect(within(kr).getByText("'코스닥' 검색 실패")).toBeInTheDocument();
    const glob = screen.getByRole("region", { name: "미국·글로벌 경제" });
    expect(within(glob).getByText("지난 24시간 동안 모인 기사가 없어요.")).toBeInTheDocument();
    expect(screen.getByLabelText("AI 요약 · news-model")).toBeChecked();
  });

  it("뉴스 키가 없으면 이유를 보여 주고, 섹션 이름만 둔 빈 화면 (새로 모으기는 못 누른다)", async () => {
    mockFetch({
      "/api/news-room": room({
        edition: null,
        updated_at: null,
        last_attempt: null,
        available: false,
        auto: false,
        reason:
          "NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 미설정 — .env 에 네이버 뉴스 검색 키를 넣고 재시작하세요",
        llm: { configured: false, model: null },
      }),
    });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    expect(await screen.findByTestId("news-reason")).toHaveTextContent("NAVER_CLIENT_ID");
    expect(screen.getByTestId("news-empty")).toHaveTextContent("아직 모은 뉴스가 없어요.");
    expect(screen.getByTestId("news-empty")).toHaveTextContent("국내 주요 이슈");
    expect(screen.getByTestId("news-updated")).toHaveTextContent("아직 없음");
    expect(screen.getByTestId("news-next")).toHaveTextContent("자동 갱신 꺼짐");
    expect(screen.getByRole("button", { name: "↻ 지금 새로 모으기" })).toBeDisabled();
    expect(screen.getByText("LLM 이 설정되지 않아 기사 목록만")).toBeInTheDocument();
  });

  it("지금 새로 모으기: 모으는 중 표시 → 끝나면 새 판과 토스트", async () => {
    const fresh: NewsEdition = {
      ...EDITION,
      id: 4,
      trigger: "manual",
      slot: null,
      created_at: "2026-11-05T07:30:00+09:00",
    };
    let calls = 0;
    const fetchMock = mockFetch({
      "/api/news-room/refresh": () => room({ refreshing: true }),
      "/api/news-room": () => {
        calls += 1;
        return calls === 1
          ? room()
          : room({ edition: fresh, updated_at: fresh.created_at, last_attempt: fresh.created_at });
      },
    });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    fireEvent.click(await screen.findByRole("button", { name: "↻ 지금 새로 모으기" }));
    expect(await screen.findByTestId("news-refreshing")).toHaveTextContent("뉴스를 모으는 중…");
    expect(screen.getByRole("button", { name: "↻ 지금 새로 모으기" })).toBeDisabled();
    const post = fetchMock.mock.calls.find(([u]) => String(u) === "/api/news-room/refresh")!;
    expect(post[1]?.method).toBe("POST");
    // 모으는 동안 1.5초마다 확인 → 끝나면 새 판
    expect(
      await screen.findByText("뉴스 룸을 새로 고쳤어요 ✓ (기사 3건)", {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("news-updated")).toHaveTextContent("수동 갱신");
    expect(screen.queryByTestId("news-refreshing")).not.toBeInTheDocument();
  });

  it("방금 갱신했으면(409) 서버 안내를 토스트로", async () => {
    mockFetch({
      "/api/news-room/refresh": () => apiError(409, "방금 갱신했어요. 42초 뒤에 다시 눌러 주세요"),
      "/api/news-room": room(),
    });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    fireEvent.click(await screen.findByRole("button", { name: "↻ 지금 새로 모으기" }));
    expect(await screen.findByText("방금 갱신했어요. 42초 뒤에 다시 눌러 주세요")).toHaveClass(
      "toast",
      "error",
    );
  });

  it("마지막 갱신이 실패했으면 이전 판과 함께 이유를 보여 준다", async () => {
    mockFetch({
      "/api/news-room": room({
        last_error: "뉴스를 하나도 받지 못했어요: 네이버 검색 하루 호출 한도 초과(HTTP 429 )",
        last_attempt: "2026-11-05T12:00:01+09:00",
      }),
    });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    expect(await screen.findByTestId("news-failed")).toHaveTextContent("이전 판을 보여 줘요");
    expect(screen.getByRole("region", { name: "세계·국제" })).toBeInTheDocument();
  });

  it("AI 요약 끄기는 PUT /api/news-room/settings", async () => {
    const fetchMock = mockFetch({
      "/api/news-room/settings": (_u: string, init?: RequestInit) =>
        room({ settings: JSON.parse(String(init?.body)) as { summary: boolean } }),
      "/api/news-room": room(),
    });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    fireEvent.click(await screen.findByLabelText("AI 요약 · news-model"));
    expect(await screen.findByText("AI 요약을 껐어요 — 다음 갱신부터")).toBeInTheDocument();
    const put = fetchMock.mock.calls.find(([u]) => String(u) === "/api/news-room/settings")!;
    expect(put[1]?.method).toBe("PUT");
    expect(JSON.parse(String(put[1]?.body))).toEqual({ summary: false });
    await waitFor(() => expect(screen.getByLabelText("AI 요약 꺼짐 — 기사 목록만")).not.toBeChecked());
  });
});

describe("증시 영향 3줄 (맨 위 섹션별 카드)", () => {
  it("AI 카드는 3줄과 근거 기사 링크, LLM 없이 만든 카드는 'AI 요약 아님'", async () => {
    mockFetch({ "/api/news-room": room() });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    const strip = await screen.findByTestId("news-impact");
    expect(within(strip).getByRole("heading", { name: "증시 영향 큰 뉴스" })).toBeInTheDocument();
    // 맨 위: 섹션별 뉴스 카드들보다 앞
    const firstSection = screen.getByRole("region", { name: "세계·국제" });
    expect(strip.compareDocumentPosition(firstSection) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const world = within(strip).getByRole("listitem", { name: "세계·국제" });
    expect(within(world).getByText("AI")).toHaveClass("news-ai");
    const lines = within(within(world).getByRole("list")).getAllByRole("listitem");
    expect(lines.map((l) => l.querySelector(".news-impact-text")?.textContent)).toEqual([
      "관세 협상 진전 — 수출주 관심",
      "근거 기사를 모르는 줄",
      "이상한 주소는 링크로 만들지 않음",
    ]);
    const link = within(world).getByRole("link", { name: "관련 기사: 미중 정상회담 개최…관세 협상 진전" });
    expect(link).toHaveAttribute("href", "https://a.example.com/1");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(within(world).getAllByRole("link")).toHaveLength(1); // 링크 없음·javascript: 는 링크로 만들지 않는다

    const kr = within(strip).getByRole("listitem", { name: "국내 경제·증시" });
    expect(within(kr).getByText("AI 요약 아님")).toHaveClass("news-rule");
    expect(
      within(kr).getByRole("link", { name: "관련 기사: 코스피 외국인 매수에 상승 출발" }),
    ).toHaveAttribute("href", "https://e.example.com/5");
    const glob = within(strip).getByRole("listitem", { name: "미국·글로벌 경제" });
    expect(within(glob).getByText("지난 24시간 기사가 없어요.")).toBeInTheDocument();
    // impact 가 없는 섹션(예전 서버)은 카드를 만들지 않는다
    expect(within(strip).queryByRole("listitem", { name: "국내 주요 이슈" })).not.toBeInTheDocument();
    // 휴대폰에서 이 상자의 가로 넘기기가 화면(탭) 넘기기보다 먼저
    expect(strip.querySelector(".news-impact-cards")).toHaveAttribute("data-no-swipe");
  });

  it("접기를 누르면 숨기고, 다시 열어도 접혀 있다 (펼치기로 되돌림)", async () => {
    mockFetch({ "/api/news-room": room() });
    const first = renderWithProviders(<NewsRoomPage />, { route: "/news" });
    const strip = await screen.findByTestId("news-impact");
    const toggle = within(strip).getByRole("button", { name: "접기" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(toggle);
    expect(within(strip).getByRole("button", { name: "펼치기" })).toHaveAttribute("aria-expanded", "false");
    expect(within(strip).queryByRole("listitem", { name: "세계·국제" })).not.toBeInTheDocument();
    expect(window.localStorage.getItem(IMPACT_OPEN_KEY)).toBe("0");
    first.unmount();

    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    const again = await screen.findByTestId("news-impact");
    fireEvent.click(within(again).getByRole("button", { name: "펼치기" }));
    expect(within(again).getByRole("listitem", { name: "세계·국제" })).toBeInTheDocument();
    expect(window.localStorage.getItem(IMPACT_OPEN_KEY)).toBe("1");
  });

  it("증시 영향이 없는 예전 서버 응답이면 띠를 그리지 않는다", async () => {
    const old: NewsEdition = {
      ...EDITION,
      sections: EDITION.sections.map((sec) => ({ ...sec, impact: undefined })),
    };
    mockFetch({ "/api/news-room": room({ edition: old }) });
    renderWithProviders(<NewsRoomPage />, { route: "/news" });
    expect(await screen.findByRole("region", { name: "세계·국제" })).toBeInTheDocument();
    expect(screen.queryByTestId("news-impact")).not.toBeInTheDocument();
    expect(impactSections(old.sections)).toEqual([]);
    expect(impactSections(EDITION.sections).map((x) => x.section.key)).toEqual([
      "world",
      "global_econ",
      "kr_market",
    ]);
  });
});

describe("뉴스 룸 도우미·연결", () => {
  it("판 이름·실패 판정·요약 안내·안전한 링크", () => {
    expect(editionLabel(EDITION)).toBe("07:00 판");
    expect(editionLabel({ ...EDITION, trigger: "manual", slot: null })).toBe("수동 갱신");
    // 서버가 꺼져 있다 06:39 에 켜지며 전날 17:00 슬롯을 따라잡은 판
    expect(editionLabel({ ...EDITION, slot: "17:00", created_at: "2026-11-05T06:39:01+09:00" })).toBe(
      "17:00 판 · 늦게 모음",
    );
    expect(editionLabel({ ...EDITION, slot: "12:00", created_at: "2026-11-05T12:00:40+09:00" })).toBe(
      "12:00 판",
    );
    expect(staleError(room())).toBeNull();
    expect(staleError(room({ last_error: "x", last_attempt: "2026-11-05T06:00:00+09:00" }))).toBeNull(); // 그 뒤에 성공
    expect(staleError(room({ last_error: "x", last_attempt: "2026-11-05T12:00:00+09:00" }))).toBe("x");
    expect(staleError(room({ edition: null, updated_at: null, last_error: "x" }))).toBe("x");
    expect(summaryNote(room({ settings: { summary: false } }))).toBe("AI 요약 꺼짐 — 기사 목록만");
    expect(safeLink("https://n.news.naver.com/a")).toBe("https://n.news.naver.com/a");
    expect(safeLink("javascript:alert(1)")).toBeNull();
  });

  it("뉴스 룸은 시장과 관계없는 화면 ('전체' 보기 유지)이고 화면 조각을 따로 받는다", () => {
    expect(isAllRoute("/news")).toBe(true);
    expect(isAllRoute("/newsletter")).toBe(false);
    expect(pageFor("/news", "KR")).not.toBeNull();
    expect(pageFor("/news", "ALL")).toBe(pageFor("/news", "KR"));
  });

  it("⌘K 팔레트에서 '뉴스'로 찾아 뉴스 룸으로 간다", async () => {
    function Where() {
      const { pathname } = useLocation();
      return <span data-testid="where">{pathname}</span>;
    }
    const onClose = vi.fn();
    mockFetch({});
    renderWithProviders(
      <>
        <CommandPalette onClose={onClose} />
        <Routes>
          <Route path="*" element={<Where />} />
        </Routes>
      </>,
    );
    const dialog = await screen.findByRole("dialog", { name: "빠른 이동" });
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "뉴스" } });
    const opts = within(dialog).getAllByRole("option");
    expect(opts[0]).toHaveTextContent("뉴스 룸");
    fireEvent.keyDown(within(dialog).getByRole("combobox"), { key: "Enter" });
    expect(onClose).toHaveBeenCalled();
    expect(screen.getByTestId("where")).toHaveTextContent("/news");
  });
});
