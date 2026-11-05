import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Position } from "../api/types";
import { Portfolio } from "../pages/Portfolio";
import { allocationItems, foldForDonut, pnlStep } from "../utils/allocation";
import { squarify } from "../utils/treemap";
import { BOTH_MARKETS, mockFetch, renderWithProviders } from "./utils";

const pos = (symbol: string, name: string, value: number, pnl = 0): Position => ({
  symbol,
  name,
  quantity: 1,
  avg_price: value,
  last_price: value,
  price_day: "2026-11-05",
  market_value: value,
  unrealized_pnl: 0,
  pnl_pct: pnl,
  weight_pct: 0,
  holding_days: 1,
});

describe("모자이크 배치 (squarify)", () => {
  const box = { x: 0, y: 0, w: 600, h: 300 };
  const items = [60, 30, 25, 20, 15, 10, 5, 3, 1].map((value, i) => ({ key: `k${i}`, value }));

  it("넓이가 값에 비례하고, 상자 안에 겹치지 않게 빈틈없이 깐다", () => {
    const out = squarify(items, box);
    expect(out).toHaveLength(items.length);
    const total = items.reduce((s, x) => s + x.value, 0);
    let area = 0;
    for (const r of out) {
      expect(r.w * r.h).toBeCloseTo((r.value / total) * box.w * box.h, 6);
      expect(r.x).toBeGreaterThanOrEqual(-1e-9);
      expect(r.y).toBeGreaterThanOrEqual(-1e-9);
      expect(r.x + r.w).toBeLessThanOrEqual(box.w + 1e-6);
      expect(r.y + r.h).toBeLessThanOrEqual(box.h + 1e-6);
      area += r.w * r.h;
    }
    expect(area).toBeCloseTo(box.w * box.h, 6);
    for (let i = 0; i < out.length; i++)
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i];
        const b = out[j];
        const overlapW = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
        const overlapH = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
        expect(overlapW > 1e-6 && overlapH > 1e-6).toBe(false);
      }
  });

  it("값이 큰 것부터, 0 이하는 빼고, 하나면 상자 전체", () => {
    const out = squarify([{ value: 0 }, { value: -3 }, { value: 7 }], box);
    expect(out).toEqual([{ value: 7, ...box }]);
    expect(squarify([], box)).toEqual([]);
    const order = squarify(items, box).map((r) => r.value);
    expect(order).toEqual([...order].sort((a, b) => b - a));
  });

  it("가로세로 비가 너무 길쭉해지지 않는다 (squarified)", () => {
    const even = Array.from({ length: 16 }, (_, i) => ({ value: 1, i }));
    for (const r of squarify(even, { x: 0, y: 0, w: 400, h: 400 }))
      expect(Math.max(r.w / r.h, r.h / r.w)).toBeLessThan(2.01);
  });
});

describe("비중 항목", () => {
  it("현금까지 합친 합계 대비 비중, 종목은 금액 큰 순·현금은 맨 뒤", () => {
    const items = allocationItems([
      { market: "KR", positions: [pos("A", "에이", 300), pos("B", "비", 600)], cash: 100 },
    ]);
    expect(items.map((x) => [x.key, x.weight])).toEqual([
      ["KR:B", 60],
      ["KR:A", 30],
      ["cash:KR", 10],
    ]);
    expect(items[2]).toMatchObject({ kind: "cash", name: "현금", pnlPct: null });
  });

  it("전체 보기: 미장은 환율을 곱해 원화로, 현금은 시장별로", () => {
    const items = allocationItems([
      { market: "KR", positions: [pos("005930", "삼성전자", 140_000)], cash: 60_000 },
      { market: "US", positions: [pos("NVDA", "엔비디아", 110)], cash: 50, rate: 1400 },
    ]);
    expect(items.map((x) => [x.key, Math.round(x.value)])).toEqual([
      ["US:NVDA", 154_000],
      ["KR:005930", 140_000],
      ["cash:KR", 60_000],
      ["cash:US", 70_000],
    ]);
    expect(items.find((x) => x.key === "cash:US")?.name).toBe("현금 (미장)");
  });

  it("도넛은 종목 8개를 넘으면 큰 7개 + '기타 N종목'(금액 가중 수익률)", () => {
    const many = Array.from({ length: 10 }, (_, i) => pos(`S${i}`, `종목${i}`, 100 - i, i === 9 ? -4 : 2));
    const folded = foldForDonut(allocationItems([{ market: "KR", positions: many, cash: 50 }]));
    expect(folded.map((x) => x.key)).toEqual([
      "KR:S0",
      "KR:S1",
      "KR:S2",
      "KR:S3",
      "KR:S4",
      "KR:S5",
      "KR:S6",
      "other",
      "cash:KR",
    ]);
    const other = folded.find((x) => x.kind === "other")!;
    expect(other.name).toBe("기타 3종목");
    expect(other.children?.map((x) => x.key)).toEqual(["KR:S7", "KR:S8", "KR:S9"]);
    expect(other.value).toBe(93 + 92 + 91);
    expect(other.pnlPct).toBeCloseTo((2 * 93 + 2 * 92 - 4 * 91) / (93 + 92 + 91), 6);
    expect(folded.reduce((s, x) => s + x.weight, 0)).toBeCloseTo(100, 6);
  });

  it("수익률 색 단계: ±0.5 · 2 · 5% 경계", () => {
    expect([0, 0.4, -0.4, 0.5, 1.9, 2, 4.9, 5, -5, -1, null].map(pnlStep)).toEqual([
      0, 0, 0, 1, 1, 2, 2, 3, -3, -1, 0,
    ]);
  });
});

// Node 의 내장 localStorage 가 jsdom 것을 가리는 환경이 있어 메모리 저장소로 바꿔 끼운다 (market.test.tsx 와 같은 방식)
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

describe("보유 종목 카드: 표 · 비중 · 모자이크 (D-68)", () => {
  beforeEach(() => vi.stubGlobal("localStorage", memoryStorage()));
  afterEach(() => vi.unstubAllGlobals());

  const KR = {
    equity: 1_000_000,
    cash: 100_000,
    positions: [pos("105560", "KB금융", 600_000, 3.67), pos("033780", "KT&G", 300_000, -1.61)],
  };

  function mockKr() {
    mockFetch({
      "/api/portfolio": KR,
      "/api/orders": [],
      "/api/fills": [],
      "/api/agent/decisions": [],
    });
  }

  it("비중을 고르면 도넛과 범례(이름·티커·비중·수익률), 모자이크를 고르면 칸(넓이 = 비중)", async () => {
    mockKr();
    renderWithProviders(<Portfolio />, { route: "/portfolio" });
    const card = (await screen.findByRole("heading", { name: "보유 종목" })).closest("section")!;
    expect(within(card).getByRole("table")).toBeInTheDocument();

    fireEvent.click(within(card).getByRole("button", { name: "비중" }));
    const donut = await within(card).findByTestId("alloc-donut");
    expect(within(donut).getByRole("img").getAttribute("aria-label")).toBe(
      "보유 비중: KB금융 60.0%, KT&G 30.0%, 현금 10.0%",
    );
    const legend = within(donut).getByRole("list", { name: "종목별 비중" });
    const rows = within(legend).getAllByRole("listitem");
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("KB금융 10556060.0%600,000원+3.67%"),
      expect.stringContaining("KT&G 03378030.0%300,000원-1.61%"),
      expect.stringContaining("현금10.0%100,000원—"),
    ]);
    // 범례에 올리면 가운데가 그 종목으로
    fireEvent.mouseEnter(rows[1]);
    expect(within(donut).getByText("KT&G", { selector: ".viz-center-name" })).toBeInTheDocument();

    fireEvent.click(within(card).getByRole("button", { name: "모자이크" }));
    const mosaic = await within(card).findByTestId("alloc-mosaic");
    const tiles = within(mosaic).getAllByRole("listitem");
    expect(tiles.map((t) => t.getAttribute("aria-label"))).toEqual([
      "KB금융 (105560), 비중 60.0% · 600,000원, 수익률 +3.67%",
      "KT&G (033780), 비중 30.0% · 300,000원, 수익률 -1.61%",
      "현금, 비중 10.0% · 100,000원",
    ]);
    expect(tiles[0]).toHaveAttribute("href", "/stock/105560");
    expect(tiles[0].className).toContain("pp2"); // +3.67% → 이익 중간
    expect(tiles[1].className).toContain("pm1"); // -1.61% → 손실 약
    expect(tiles[2].className).toContain("cash");
    // 넓이 비 = 비중 비 (기본 640×320 상자, 칸 사이 2px 틈 빼고)
    const area = (t: HTMLElement) => (parseFloat(t.style.width) + 2) * (parseFloat(t.style.height) + 2);
    expect(area(tiles[0]) / area(tiles[1])).toBeCloseTo(2, 3);
    // 고른 보기는 이 기기에 기억
    expect(window.localStorage.getItem("portfolio.view")).toBe("mosaic");
  });

  it("전체 보기에는 국장·미장을 원화로 합친 비중 카드", async () => {
    mockFetch({
      "/api/us/portfolio": { equity: 160, cash: 50, positions: [pos("NVDA", "엔비디아", 110, 1)] },
      "/api/us/orders": [],
      "/api/us/fills": [],
      "/api/us/agent/decisions": [],
      "/api/portfolio": { equity: 200_000, cash: 60_000, positions: [pos("005930", "삼성전자", 140_000)] },
      "/api/orders": [],
      "/api/fills": [],
      "/api/agent/decisions": [],
      "/api/overview": {
        fx: { usdkrw: 1400, at: null },
        markets: [],
        total_krw: { equity: 410_000, pnl_today: 0 },
      },
    });
    renderWithProviders(<Portfolio />, { markets: BOTH_MARKETS, market: "ALL", route: "/portfolio" });
    const card = await screen.findByRole("region", { name: "전체 보유 비중" });
    await waitFor(() =>
      expect(within(card).getByRole("img").getAttribute("aria-label")).toBe(
        "보유 비중: 엔비디아 36.3%, 삼성전자 33.0%, 현금 (국장) 14.2%, 현금 (미장) 16.5%",
      ),
    );
    expect(within(card).getByText(/1,400원으로 바꿔/)).toBeInTheDocument();
    // 합계는 위 '전체 자산' 카드에만 (가운데는 종목 수·현금 비중)
    expect(within(card).getByText("국장 + 미장")).toBeInTheDocument();
    expect(within(card).getByText("2종목")).toBeInTheDocument();
  });

  it("도넛의 '기타 N종목'을 누르면 묶인 종목이 범례에 펼쳐진다", async () => {
    const many = Array.from({ length: 10 }, (_, i) => pos(`S${i}`, `종목${i}`, 100 - i));
    mockFetch({
      "/api/portfolio": { equity: 1000, cash: 45, positions: many },
      "/api/orders": [],
      "/api/fills": [],
      "/api/agent/decisions": [],
    });
    window.localStorage.setItem("portfolio.view", "donut");
    renderWithProviders(<Portfolio />, { route: "/portfolio" });
    const donut = await screen.findByTestId("alloc-donut");
    const legend = within(donut).getByRole("list", { name: "종목별 비중" });
    expect(within(legend).queryByText("종목9")).not.toBeInTheDocument();
    const toggle = within(legend).getByRole("button", { name: /기타 3종목/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(within(legend).getByText("종목9")).toBeInTheDocument();
    const rows = within(legend)
      .getAllByRole("listitem")
      .map((r) => r.textContent ?? "");
    expect(rows).toHaveLength(9 + 3);
    // 묶인 종목은 '기타' 줄 바로 아래, 현금은 그 뒤
    const at = rows.findIndex((t) => t.startsWith("기타 3종목"));
    expect(rows.slice(at + 1, at + 4).map((t) => t.slice(0, 3))).toEqual(["종목7", "종목8", "종목9"]);
    expect(rows.at(-1)).toMatch(/^현금/);
  });
});
