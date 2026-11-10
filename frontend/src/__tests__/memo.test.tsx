import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MemoCard, MemoCardCreate, MemoryTopic, StockSearchItem } from "../api/types";
import { MemoPage } from "../pages/Memo";
import {
  addDays,
  addMonths,
  expiryFor,
  fmtDay,
  groupCards,
  isExpiredOn,
  looksLikeCode,
  memoApi,
  presetOf,
  todayKst,
} from "../utils/memo";
import { apiError, mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const card = (over: Partial<MemoCard> & Pick<MemoCard, "id" | "kind">): MemoCard => ({
  symbol: null,
  name: null,
  text: "",
  active: true,
  expires_on: null,
  expired: false,
  created_at: "2026-11-01T09:00:00+09:00",
  updated_at: "2026-11-01T09:00:00+09:00",
  ...over,
});

const CARDS: MemoCard[] = [
  card({ id: 1, kind: "watch", symbol: "005930", name: "삼성전자", text: "실적 발표 전까지 지켜보기" }),
  card({ id: 2, kind: "watch", symbol: "035720", name: "카카오", active: false }),
  card({
    id: 3,
    kind: "focus",
    symbol: "000660",
    name: "SK하이닉스",
    text: "HBM 수주 확인되면 매수 검토",
    expires_on: "2099-12-31",
  }),
  card({ id: 4, kind: "avoid", symbol: "068270", name: "셀트리온", expires_on: "2026-10-31", expired: true }),
  card({ id: 5, kind: "note", text: "이번 주는 현금 비중 30% 이상 유지" }),
];

const STOCKS: StockSearchItem[] = [
  { symbol: "005930", name: "삼성전자", market: "KOSPI" },
  { symbol: "009150", name: "삼성전기", market: "KOSPI" },
  { symbol: "005935", name: "삼성전자우", market: "KOSPI" },
  { symbol: "000660", name: "SK하이닉스", market: "KOSPI" },
];

type Method = "POST" | "PATCH" | "DELETE" | "PUT";

const topic = (over: Partial<MemoryTopic> & Pick<MemoryTopic, "id" | "key" | "label">): MemoryTopic => ({
  symbol: null,
  score: 0.52,
  included: true,
  pin: "",
  days: 2,
  streak: 2,
  mentions: 2,
  last_day: "2026-11-04",
  quotes: [{ id: 9, at: "2026-11-04T08:00:00+09:00", text: "카카오 오늘은 어때?" }],
  llm_level: 1,
  llm_reason: "이틀 연달아 물어봄",
  forgotten: false,
  ...over,
});

const TOPICS: MemoryTopic[] = [
  topic({ id: 1, key: "sym:035720", label: "카카오(035720)", symbol: "035720" }),
  topic({
    id: 2,
    key: "t:반도체",
    label: "반도체",
    score: 0.2,
    included: false,
    days: 1,
    streak: 1,
    mentions: 1,
    last_day: "2026-11-02",
    llm_level: -1,
    llm_reason: "한 번 묻고 끝",
    quotes: [],
  }),
];

/** 메모 카드 API 를 흉내 내는 작은 서버 (요청에 따라 상태가 바뀐다) */
function memoServer(
  opts: {
    fail?: Partial<Record<Method, () => Response>>;
    gate?: Promise<void>;
    cards?: MemoCard[];
    topics?: MemoryTopic[];
    llm?: boolean;
  } = {},
) {
  let cards = [...(opts.cards ?? CARDS)];
  let topics = [...(opts.topics ?? TOPICS)];
  let settings = { topics: true, threshold: 0.35, max_in_prompt: 8 };
  const topicsBody = (result?: object) => ({
    topics,
    settings,
    review: { last_msg_id: 9, at: null, fails: 0 },
    llm: opts.llm ?? true,
    today: "2026-11-04",
    ...(result ? { result } : {}),
  });
  let nextId = 100;
  const fetchMock = mockFetch({
    "/api/memory/cards": async (url: string, init?: RequestInit) => {
      const method = (init?.method ?? "GET") as Method | "GET";
      if (method === "GET") return { cards, settings };
      if (opts.gate) await opts.gate;
      const failure = opts.fail?.[method];
      if (failure) return failure();
      const body = init?.body ? JSON.parse(String(init.body)) : {};
      const id = Number(url.split("/").pop());
      if (method === "POST") {
        const b = body as MemoCardCreate;
        const c = card({
          id: nextId++,
          kind: b.kind,
          symbol: b.symbol ?? null,
          name: STOCKS.find((s) => s.symbol === b.symbol)?.name ?? null,
          text: b.text ?? "",
          expires_on: b.expires_on ?? null,
        });
        cards = [...cards, c];
        return c;
      }
      if (method === "PATCH") {
        cards = cards.map((c) =>
          c.id === id ? { ...c, ...body, updated_at: "2026-11-04T10:00:00+09:00" } : c,
        );
        return cards.find((c) => c.id === id);
      }
      if (method === "DELETE") {
        cards = cards.filter((c) => c.id !== id);
        return new Response(null, { status: 204 });
      }
      return apiError(405, "method");
    },
    "/api/memory/settings": async (_url: string, init?: RequestInit) => {
      if (opts.gate) await opts.gate;
      const failure = opts.fail?.PUT;
      if (failure) return failure();
      settings = { ...settings, ...JSON.parse(String(init?.body)) };
      return settings;
    },
    "/api/memory/topics": (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      if (method === "GET") return topicsBody();
      if (url.endsWith("/review")) {
        topics = topics.map((t) => (t.id === 2 ? { ...t, score: 0.4, included: true } : t));
        return topicsBody({ messages: 3, mentioned: 1, created: 0, adjusted: 1, dropped: 0 });
      }
      const id = Number(url.split("/").pop());
      if (method === "DELETE") {
        topics = topics.filter((t) => t.id !== id);
        return new Response(null, { status: 204 });
      }
      const { pin } = JSON.parse(String(init?.body));
      topics = topics.map((t) =>
        t.id === id ? { ...t, pin, included: pin === "pin" || (pin === "" && t.score >= 0.35) } : t,
      );
      return topics.find((t) => t.id === id);
    },
    "/api/market/search": (url: string) => {
      const q = new URL(url, "http://test").searchParams.get("q") ?? "";
      return STOCKS.filter((s) => s.symbol.startsWith(q) || s.name.includes(q));
    },
  });
  const sent = (method: string, prefix = "/api/memory") =>
    fetchMock.mock.calls
      .filter(([u, i]) => String(u).startsWith(prefix) && (i?.method ?? "GET") === method)
      .map(([u, i]) => ({ url: String(u), body: i?.body ? JSON.parse(String(i.body)) : undefined }));
  const searches = () =>
    fetchMock.mock.calls
      .map(([u]) => String(u))
      .filter((u) => u.startsWith("/api/market/search"))
      .map((u) => new URL(u, "http://test").searchParams.get("q"));
  return { fetchMock, sent, searches };
}

const group = (name: string) => screen.getByRole("region", { name });
const loaded = () => screen.findByRole("region", { name: "관심 종목" });

describe("메모 카드 화면", () => {
  it("카드를 종류별로 묶고, 묶음별 개수와 켜진 카드 수를 보여 준다", async () => {
    memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    expect(
      screen.getByText("매니저는 판단·답장 때마다 켜져 있는 카드만 기억으로 받아요. 끄거나 만료되면 빠져요."),
    ).toBeInTheDocument();
    // 켜져 있고 만료 안 된 카드: 삼성전자 · SK하이닉스 · 운용 지시
    expect(screen.getByTestId("memo-live-count")).toHaveTextContent("켜진 카드 3개");

    const watch = within(group("관심 종목")).getAllByRole("listitem");
    expect(watch).toHaveLength(2);
    expect(screen.getByTestId("memo-count-watch")).toHaveTextContent("2개 · 켜짐 1");
    expect(screen.getByTestId("memo-count-note")).toHaveTextContent("1개");
    expect(within(group("주목할 종목")).getByRole("link", { name: /SK하이닉스/ })).toHaveAttribute(
      "href",
      "/stock/000660",
    );
    expect(within(group("주목할 종목")).getByText("12/31(목)까지")).toBeInTheDocument();
    expect(within(group("운용 지시")).getByText("이번 주는 현금 비중 30% 이상 유지")).toBeInTheDocument();

    // 꺼진 카드·만료된 카드는 흐리게
    const kakao = screen.getByRole("listitem", { name: "관심 종목 · 카카오" });
    expect(kakao).toHaveClass("dim");
    expect(within(kakao).getByRole("switch", { name: "카카오 켜기" })).not.toBeChecked();
    const celltrion = screen.getByRole("listitem", { name: "피할 종목 · 셀트리온" });
    expect(celltrion).toHaveClass("dim", "expired");
    expect(within(celltrion).getByText("10/31(토) 만료됨")).toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "관심 종목 · 삼성전자" })).not.toHaveClass("dim");
  });

  it("켜기 스위치를 누르면 PATCH 로 보내고 개수가 바로 바뀐다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.click(screen.getByRole("switch", { name: "카카오 켜기" }));
    expect(await screen.findByTestId("memo-live-count")).toHaveTextContent("켜진 카드 4개");
    await waitFor(() => expect(sent("PATCH")).toHaveLength(1));
    expect(sent("PATCH")[0]).toEqual({ url: "/api/memory/cards/2", body: { active: true } });
    expect(screen.getByRole("switch", { name: "카카오 켜기" })).toBeChecked();
    expect(screen.getByRole("listitem", { name: "관심 종목 · 카카오" })).not.toHaveClass("dim");
  });

  it("먼저 화면을 바꾸고, 서버가 거절하면 되돌린 뒤 오류를 보여 준다", async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    memoServer({ gate, fail: { PATCH: () => apiError(400, "카드를 바꿀 수 없어요") } });
    renderWithProviders(<MemoPage />);
    await loaded();
    const sw = screen.getByRole("switch", { name: "삼성전자 켜기" });
    expect(sw).toBeChecked();
    fireEvent.click(sw);
    // 서버 응답 전: 이미 꺼진 것처럼 보인다
    await waitFor(() => expect(sw).not.toBeChecked());
    expect(screen.getByTestId("memo-live-count")).toHaveTextContent("켜진 카드 2개");
    release();
    expect(await screen.findByRole("alert")).toHaveTextContent("카드를 바꿀 수 없어요");
    expect(screen.getByRole("switch", { name: "삼성전자 켜기" })).toBeChecked();
    expect(screen.getByTestId("memo-live-count")).toHaveTextContent("켜진 카드 3개");
  });

  it("메모 글을 눌러 고치면 바뀐 칸만 PATCH 로 보낸다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    const item = screen.getByRole("listitem", { name: "주목할 종목 · SK하이닉스" });
    fireEvent.click(within(item).getByRole("button", { name: /SK하이닉스 메모: HBM 수주/ }));
    const box = within(item).getByRole("textbox");
    expect(box).toHaveValue("HBM 수주 확인되면 매수 검토");
    fireEvent.change(box, { target: { value: "  HBM 수주 확정 — 분할 매수  " } });
    fireEvent.click(within(item).getByRole("button", { name: "저장" }));
    expect(await within(item).findByText("HBM 수주 확정 — 분할 매수")).toBeInTheDocument();
    await waitFor(() => expect(sent("PATCH")).toHaveLength(1));
    expect(sent("PATCH")[0]).toEqual({
      url: "/api/memory/cards/3",
      body: { text: "HBM 수주 확정 — 분할 매수" },
    });
  });

  it("고치기에서 만료일을 바꾸거나 없앨 수 있다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    const item = screen.getByRole("listitem", { name: "주목할 종목 · SK하이닉스" });
    fireEvent.click(within(item).getByRole("button", { name: "SK하이닉스 고치기" }));
    fireEvent.click(
      within(within(item).getByRole("group", { name: "만료일" })).getByRole("button", { name: "없음" }),
    );
    fireEvent.click(within(item).getByRole("button", { name: "저장" }));
    expect(await within(item).findByText("만료 없음")).toBeInTheDocument();
    await waitFor(() => expect(sent("PATCH")).toHaveLength(1));
    expect(sent("PATCH")[0].body).toEqual({ expires_on: null });
  });

  it("운용 지시를 비우면 저장하지 않고 알려 준다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    const note = within(group("운용 지시")).getAllByRole("listitem")[0];
    fireEvent.click(within(note).getByRole("button", { name: /고치기$/ }));
    fireEvent.change(within(note).getByRole("textbox"), { target: { value: "   " } });
    fireEvent.click(within(note).getByRole("button", { name: "저장" }));
    expect(within(note).getByRole("alert")).toHaveTextContent("운용 지시는 비울 수 없어요");
    expect(sent("PATCH")).toHaveLength(0);
  });

  it("삭제는 확인한 뒤 지우고, 실패하면 카드를 되살린다", async () => {
    const ok = memoServer();
    const { unmount } = renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "삼성전자 삭제" }));
    const dialog = screen.getByRole("dialog", { name: "카드를 지울까요?" });
    expect(dialog).toHaveTextContent("삼성전자");
    fireEvent.click(within(dialog).getByRole("button", { name: "지우기" }));
    await waitFor(() =>
      expect(screen.queryByRole("listitem", { name: "관심 종목 · 삼성전자" })).not.toBeInTheDocument(),
    );
    expect(screen.getByTestId("memo-count-watch")).toHaveTextContent("1개");
    await waitFor(() => expect(ok.sent("DELETE")).toEqual([{ url: "/api/memory/cards/1", body: undefined }]));
    unmount();
    vi.unstubAllGlobals();

    memoServer({ fail: { DELETE: () => apiError(500, "DB 오류") } });
    renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "삼성전자 삭제" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "지우기" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DB 오류");
    expect(screen.getByRole("listitem", { name: "관심 종목 · 삼성전자" })).toBeInTheDocument();
  });

  it("삭제 확인 창에서 취소하면 지우지 않는다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "카카오 삭제" }));
    fireEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "취소" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("listitem", { name: "관심 종목 · 카카오" })).toBeInTheDocument();
    expect(sent("DELETE")).toHaveLength(0);
  });
});

describe("메모 카드 추가", () => {
  it("종목 검색은 250ms 멈춘 뒤 한 번만 하고, ↑/↓·Enter 로 골라 카드를 추가한다", async () => {
    const { sent, searches } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "주목할 종목" }));
    const box = screen.getByRole("combobox", { name: "종목" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "삼" } });
    fireEvent.change(box, { target: { value: "삼성" } });
    expect(searches()).toHaveLength(0); // 아직 치는 중

    const opts = await screen.findAllByRole("option");
    expect(searches()).toEqual(["삼성"]);
    expect(opts).toHaveLength(3);
    expect(opts[0]).toHaveTextContent("삼성전자 · 005930 · KOSPI");
    expect(opts[0]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(screen.getAllByRole("option")[1]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    fireEvent.keyDown(box, { key: "ArrowUp" });
    fireEvent.keyDown(box, { key: "Enter" });
    expect(screen.getByTestId("memo-picked")).toHaveTextContent("삼성전기 · 009150 · KOSPI");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/메모 \(선택\)/), { target: { value: "  전장 수주 지켜보기 " } });
    const expiry = screen.getByRole("group", { name: "만료일 (선택)" });
    fireEvent.click(within(expiry).getByRole("button", { name: "1주" }));
    expect(within(expiry).getByRole("button", { name: "1주" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "카드 추가" }));

    expect(await within(group("주목할 종목")).findByText("삼성전기")).toBeInTheDocument();
    expect(sent("POST")).toEqual([
      {
        url: "/api/memory/cards",
        body: {
          kind: "focus",
          symbol: "009150",
          text: "전장 수주 지켜보기",
          expires_on: addDays(todayKst(), 7),
        },
      },
    ]);
    expect(screen.getByText(/추가했어요 ✓/)).toBeInTheDocument();
    expect(screen.getByTestId("memo-live-count")).toHaveTextContent("켜진 카드 4개");
    // 추가한 뒤에는 칸을 비운다
    expect(screen.getByRole("combobox", { name: "종목" })).toHaveValue("");
  });

  it("검색 결과를 눌러 고르고, 서버가 거절하면(중복 등) 이유를 보여 준다", async () => {
    const { sent } = memoServer({
      fail: { POST: () => apiError(400, "이미 켜진 같은 카드가 있어요 (삼성전자 · 관심 종목)") },
    });
    renderWithProviders(<MemoPage />);
    await loaded();
    const box = screen.getByRole("combobox", { name: "종목" });
    fireEvent.focus(box);
    fireEvent.change(box, { target: { value: "0059" } });
    const opt = await screen.findByRole("option", { name: /삼성전자 · 005930/ });
    fireEvent.mouseDown(opt);
    fireEvent.click(opt);
    expect(screen.getByTestId("memo-picked")).toHaveTextContent("삼성전자 · 005930 · KOSPI");
    fireEvent.click(screen.getByRole("button", { name: "카드 추가" }));
    expect(
      await screen.findByText("이미 켜진 같은 카드가 있어요 (삼성전자 · 관심 종목)"),
    ).toBeInTheDocument();
    expect(sent("POST")[0].body).toEqual({ kind: "watch", symbol: "005930", expires_on: null });
    // 고른 종목은 그대로 남아 있어 다시 시도할 수 있다
    expect(screen.getByTestId("memo-picked")).toBeInTheDocument();
  });

  it("종목을 고르지 않으면 요청하지 않는다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.change(screen.getByRole("combobox", { name: "종목" }), { target: { value: "삼성" } });
    fireEvent.click(screen.getByRole("button", { name: "카드 추가" }));
    expect(screen.getByText(/종목을 검색해서 골라 주세요/)).toBeInTheDocument();
    expect(sent("POST")).toHaveLength(0);
  });

  it("운용 지시는 내용을 적어야 추가된다", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    await loaded();
    fireEvent.click(screen.getByRole("button", { name: "운용 지시" }));
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "카드 추가" }));
    expect(screen.getByText("운용 지시 내용을 적어 주세요")).toBeInTheDocument();
    expect(sent("POST")).toHaveLength(0);

    fireEvent.change(screen.getByRole("textbox", { name: "운용 지시" }), {
      target: { value: "장 초반 30분은 매수하지 마" },
    });
    fireEvent.click(
      within(screen.getByRole("group", { name: "만료일 (선택)" })).getByRole("button", { name: "오늘까지" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "카드 추가" }));
    expect(await within(group("운용 지시")).findByText("장 초반 30분은 매수하지 마")).toBeInTheDocument();
    expect(sent("POST")[0].body).toEqual({
      kind: "note",
      text: "장 초반 30분은 매수하지 마",
      expires_on: todayKst(),
    });
  });
});

describe("대화에서 기억하기 (D-67)", () => {
  const topicCard = () => screen.findByRole("region", { name: "대화에서 기억하기" });

  it("주제마다 중요도·언급한 날·매니저 판단과 이유·최근 인용을 보여 준다", async () => {
    memoServer();
    renderWithProviders(<MemoPage />);
    const card = await topicCard();
    const kakao = await within(card).findByTestId("topic-sym:035720");
    expect(kakao).toHaveTextContent("카카오(035720)");
    expect(kakao).toHaveTextContent("기억 중");
    expect(within(kakao).getByLabelText("중요도 52")).toBeInTheDocument();
    expect(kakao).toHaveTextContent("2일 언급 · 연달아 2일 · 마지막 11/04 · 메시지 2개");
    expect(kakao).toHaveTextContent("매니저 ⬆ 올림 — 이틀 연달아 물어봄");
    expect(kakao).toHaveTextContent("카카오 오늘은 어때?");
    const chip = within(card).getByTestId("topic-t:반도체");
    expect(chip).toHaveTextContent("빠짐");
    expect(chip).toHaveTextContent("매니저 ⬇ 내림 — 한 번 묻고 끝");
    expect(within(card).getByTestId("topic-in-count")).toHaveTextContent(
      "지금 기억에 들어가는 주제 1개 · 중요도 35 이상, 최대 8개",
    );
    expect(card).toHaveTextContent("여러 날 되풀이해 물으면");
  });

  it("늘 기억·빼기를 PATCH 로 보내고, 지우기는 DELETE", async () => {
    const { sent } = memoServer();
    renderWithProviders(<MemoPage />);
    const card = await topicCard();
    const chip = await within(card).findByTestId("topic-t:반도체");
    fireEvent.click(within(chip).getByRole("button", { name: "늘 기억" }));
    await waitFor(() =>
      expect(within(chip).getByText("늘 기억", { selector: ".topic-state" })).toBeInTheDocument(),
    );
    expect(sent("PATCH")).toEqual([{ url: "/api/memory/topics/2", body: { pin: "pin" } }]);
    expect(within(chip).getByRole("button", { name: "늘 기억" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(within(chip).getByRole("button", { name: "지우기" }));
    await waitFor(() => expect(within(card).queryByTestId("topic-t:반도체")).not.toBeInTheDocument());
    expect(sent("DELETE")).toEqual([{ url: "/api/memory/topics/2", body: undefined }]);
  });

  it("'지금 정리'는 POST 하고 새 목록으로 바꾼다 — LLM 이 없으면 누를 수 없다", async () => {
    const { sent } = memoServer();
    const { unmount } = renderWithProviders(<MemoPage />);
    const card = await topicCard();
    await within(card).findByTestId("topic-t:반도체");
    fireEvent.click(within(card).getByRole("button", { name: "지금 정리" }));
    await waitFor(() => expect(within(card).getByTestId("topic-t:반도체")).toHaveTextContent("기억 중"));
    expect(sent("POST")).toEqual([{ url: "/api/memory/topics/review", body: {} }]);
    unmount();
    memoServer({ llm: false });
    renderWithProviders(<MemoPage />);
    const card2 = await topicCard();
    await within(card2).findByTestId("topic-t:반도체");
    expect(within(card2).getByRole("button", { name: "지금 정리" })).toBeDisabled();
  });

  it("끄면 PUT 으로 보내고, 실패하면 스위치를 되돌린다", async () => {
    const { sent } = memoServer();
    const { unmount } = renderWithProviders(<MemoPage />);
    await loaded();
    const sw = screen.getByRole("switch", { name: "대화에서 기억하기" });
    expect(sw).toBeChecked();
    fireEvent.click(sw);
    await waitFor(() => expect(sent("PUT")).toHaveLength(1));
    expect(sent("PUT")[0]).toEqual({ url: "/api/memory/settings", body: { topics: false } });
    expect(sw).not.toBeChecked();
    unmount();
    memoServer({ fail: { PUT: () => apiError(500, "설정을 저장하지 못했어요") } });
    renderWithProviders(<MemoPage />);
    await loaded();
    const sw2 = screen.getByRole("switch", { name: "대화에서 기억하기" });
    fireEvent.click(sw2);
    expect(await screen.findByText("설정을 저장하지 못했어요")).toBeInTheDocument();
    expect(sw2).toBeChecked();
  });
});

describe("메모 카드 도우미", () => {
  it("빠른 만료일: 오늘까지 · 1주 · 1달(말일 보정) · 없음", () => {
    expect(expiryFor("today", "2026-11-04")).toBe("2026-11-04");
    expect(expiryFor("week", "2026-11-04")).toBe("2026-11-11");
    expect(expiryFor("month", "2026-11-04")).toBe("2026-12-04");
    expect(expiryFor("none", "2026-11-04")).toBeNull();
    expect(addMonths("2026-01-31", 1)).toBe("2026-02-28");
    expect(addMonths("2026-12-15", 1)).toBe("2027-01-15");
    expect(addDays("2026-12-28", 7)).toBe("2027-01-04");
    expect(presetOf("2026-11-11", "2026-11-04")).toBe("week");
    expect(presetOf(null, "2026-11-04")).toBe("none");
    expect(presetOf("2026-11-20", "2026-11-04")).toBeNull();
  });

  it("한국 날짜·요일·만료 판단", () => {
    expect(todayKst(new Date("2026-11-04T15:30:00Z"))).toBe("2026-11-05"); // KST 자정이 지났다
    expect(fmtDay("2026-11-11")).toBe("11/11(수)");
    expect(isExpiredOn("2026-11-03", "2026-11-04")).toBe(true);
    expect(isExpiredOn("2026-11-04", "2026-11-04")).toBe(false); // 그날까지는 유효
    expect(isExpiredOn(null, "2026-11-04")).toBe(false);
  });

  it("종류별로 묶고 묶음 안에서는 새 카드가 먼저", () => {
    const g = groupCards(CARDS);
    expect(g.watch.map((c) => c.id)).toEqual([2, 1]);
    expect(g.focus.map((c) => c.id)).toEqual([3]);
    expect(g.note.map((c) => c.id)).toEqual([5]);
  });

  it("종목코드처럼 보이는 입력 · API 주소는 시장 접두어로 바꿀 수 있다", () => {
    expect(looksLikeCode("005930")).toBe(true);
    expect(looksLikeCode("0001a0")).toBe(true);
    expect(looksLikeCode("삼성전자")).toBe(false);
    expect(looksLikeCode("12345")).toBe(false);
    const us = memoApi("/api/us").paths;
    expect(us.cards).toBe("/api/us/memory/cards");
    expect(us.card(3)).toBe("/api/us/memory/cards/3");
    expect(memoApi().paths.search("삼성 전자")).toBe(
      "/api/market/search?q=%EC%82%BC%EC%84%B1%20%EC%A0%84%EC%9E%90&limit=20",
    );
  });
});
