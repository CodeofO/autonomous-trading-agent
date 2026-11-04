import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ExtendedHoursInfo, ExtendedSession } from "../api/types";
import { ExtendedHoursCard } from "../pages/ExtendedHoursCard";
import { sessionTimeText } from "../utils/extended";
import { mockFetch, renderWithProviders } from "./utils";

afterEach(() => vi.unstubAllGlobals());

const PRE: ExtendedSession = {
  key: "pre_market",
  label: "프리마켓",
  hint: "NXT 프리마켓 (정규장 시작 전)",
  enabled: false,
  active: false,
  next: {
    day: "2026-11-05",
    start: "2026-11-05T08:00:00+09:00",
    end: "2026-11-05T08:50:00+09:00",
    start_local: "08:00",
    end_local: "08:50",
  },
};
const AFTER: ExtendedSession = {
  ...PRE,
  key: "after_market",
  label: "애프터마켓",
  hint: "NXT 애프터마켓 (정규장 마감 뒤)",
  active: true,
  next: { ...PRE.next!, start: "2026-11-04T15:40:00+09:00", end: "2026-11-04T20:00:00+09:00" },
};

function kr(over: Partial<ExtendedHoursInfo> = {}): ExtendedHoursInfo {
  return {
    market: "KR",
    timezone: "Asia/Seoul",
    live: false,
    calendar_known: true,
    sessions: [PRE, AFTER],
    order_hours: "08:30~15:20 거래일만 접수",
    ...over,
  };
}

// 미장 데이마켓: 서머타임이 끝난 뒤 — 뉴욕 시각은 토스가 준 시각에서 바로 나온다
const US_DAY: ExtendedSession = {
  key: "day_market",
  label: "데이마켓",
  hint: "한국 낮 시간 미국 주식 거래 (토스증권 데이마켓)",
  enabled: false,
  active: false,
  next: {
    day: "2026-11-05",
    start: "2026-11-05T09:00:00+09:00",
    end: "2026-11-05T16:50:00+09:00",
    start_local: "19:00",
    end_local: "02:50",
  },
};

describe("장외 주문 카드", () => {
  it("세션 시각을 한국 시각(미장은 뉴욕 시각도)으로 보여 준다", () => {
    expect(sessionTimeText(PRE, "KR")).toBe("08:00~08:50");
    expect(sessionTimeText(US_DAY, "US")).toBe("한국 09:00~16:50 · 뉴욕 19:00~02:50");
    expect(sessionTimeText({ ...PRE, next: null }, "KR")).toBe("시각 정보 없음");
  });

  it("스위치로 켜면 그 세션만 PUT 하고 결과를 반영한다", async () => {
    let state = kr();
    const bodies: unknown[] = [];
    mockFetch({
      "/api/extended-hours": (_u: string, init?: RequestInit) => {
        if (init?.method === "PUT") {
          const body = JSON.parse(String(init.body)) as Record<string, boolean>;
          bodies.push(body);
          state = kr({
            sessions: state.sessions.map((s) => (s.key in body ? { ...s, enabled: body[s.key] } : s)),
          });
        }
        return state;
      },
      "/api/us/extended-hours": { ...kr(), market: "US", sessions: [US_DAY] },
    });
    renderWithProviders(<ExtendedHoursCard />);
    const sw = await screen.findByRole("checkbox", { name: "애프터마켓 주문" });
    expect(screen.getByText("진행 중")).toBeInTheDocument();
    expect(sw).not.toBeChecked();
    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByRole("checkbox", { name: "애프터마켓 주문" })).toBeChecked());
    expect(bodies).toEqual([{ after_market: true }]);
    expect(screen.getByText(/세션이 끝나면 미체결분을 취소/)).toBeInTheDocument();
  });

  it("실거래 계좌에서 켜 두면 실제 주문이 나간다고 경고한다", async () => {
    mockFetch({
      "/api/extended-hours": kr({ live: true, sessions: [{ ...PRE, enabled: true }, AFTER] }),
      "/api/us/extended-hours": { ...kr(), market: "US", sessions: [US_DAY] },
    });
    renderWithProviders(<ExtendedHoursCard />);
    expect(await screen.findByText(/실제 주문이 나가요/)).toBeInTheDocument();
  });

  it("토스 장 운영 정보를 못 받았으면 장외 주문을 받지 않는다고 알린다", async () => {
    mockFetch({
      "/api/extended-hours": kr({ calendar_known: false }),
      "/api/us/extended-hours": { ...kr(), market: "US", sessions: [US_DAY] },
    });
    renderWithProviders(<ExtendedHoursCard />);
    expect(await screen.findByText(/토스 장 운영 정보를 받지 못했어요/)).toBeInTheDocument();
  });
});
