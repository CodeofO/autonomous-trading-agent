import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { PushStatus } from "../api/types";
import { PushSettings } from "../pages/settings/AlertsSection";
import { setDeviceId } from "../push";
import { mockFetch, renderWithProviders } from "./utils";

const IPHONE_SAFARI =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const ENDPOINT = "https://push.example.com/send/this-device";
const PUBLIC_KEY = "BAECAwQ"; // base64url([4, 1, 2, 3, 4]) — 테스트용 가짜 키

const status = (over: Partial<PushStatus> = {}): PushStatus => ({
  available: true,
  reason: null,
  public_key: PUBLIC_KEY,
  devices: [],
  prefs: { fills: true, risk: true, alerts: true, agent: false },
  ...over,
});

const DEVICE = {
  id: "dev-1",
  label: "iPhone · Safari",
  created_at: "2026-11-01T09:00:00+09:00",
  last_ok: null,
};
const OTHER = { id: "dev-2", label: "Mac · Chrome", created_at: "2026-10-30T09:00:00+09:00", last_ok: null };

interface FakeSub {
  endpoint: string;
  options: { applicationServerKey: ArrayBuffer | null };
  toJSON: () => PushSubscriptionJSON;
  unsubscribe: ReturnType<typeof vi.fn>;
}

/** 브라우저 알림 기능을 흉내 낸다 (HTTPS · 서비스 워커 · 푸시 · 알림 권한) */
function setupBrowser(
  opts: {
    secure?: boolean;
    ua?: string;
    standalone?: boolean;
    push?: boolean;
    permission?: NotificationPermission;
    grant?: NotificationPermission;
    subscribed?: boolean;
  } = {},
) {
  const { secure = true, ua = IPHONE_SAFARI, standalone = true, push = true } = opts;
  vi.stubGlobal("isSecureContext", secure);
  vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
  Object.defineProperty(navigator, "userAgent", { value: ua, configurable: true });
  Object.defineProperty(navigator, "standalone", { value: standalone, configurable: true });

  const notification = {
    permission: opts.permission ?? "default",
    requestPermission: vi.fn(async () => {
      notification.permission = opts.grant ?? "granted";
      return notification.permission;
    }),
  };
  vi.stubGlobal("Notification", notification);

  let current: FakeSub | null = null;
  const makeSub = (key: ArrayBuffer | null): FakeSub => ({
    endpoint: ENDPOINT,
    options: { applicationServerKey: key },
    toJSON: () => ({ endpoint: ENDPOINT, keys: { p256dh: "p256-key", auth: "auth-key" } }),
    unsubscribe: vi.fn(async () => {
      current = null;
      return true;
    }),
  });
  if (opts.subscribed) current = makeSub(new Uint8Array([4, 1, 2, 3, 4]).buffer);
  const pushManager = {
    getSubscription: vi.fn(async () => current),
    subscribe: vi.fn(async (o: PushSubscriptionOptionsInit) => {
      current = makeSub((o.applicationServerKey as Uint8Array).buffer as ArrayBuffer);
      return current;
    }),
  };
  const reg = { pushManager };
  if (push) {
    vi.stubGlobal("PushManager", function PushManager() {});
    Object.defineProperty(navigator, "serviceWorker", {
      value: {
        getRegistration: vi.fn(async () => reg),
        register: vi.fn(async () => reg),
        ready: Promise.resolve(reg),
      },
      configurable: true,
    });
  }
  return { notification, pushManager, sub: () => current };
}

/** /api/push 전체를 하나의 가짜 서버로 (요청 기록 포함) */
function mockPushApi(initial: PushStatus) {
  const state = { ...initial };
  const calls: { method: string; url: string; body: unknown }[] = [];
  const fetchMock = mockFetch({
    "/api/push": (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(init.body as string) : undefined;
      calls.push({ method, url, body });
      if (url === "/api/push/subscribe") {
        state.devices = [...state.devices, { ...DEVICE, label: body.label }];
        return { id: DEVICE.id, devices: state.devices.length };
      }
      if (url === "/api/push/unsubscribe") {
        state.devices = state.devices.filter((d) => d.id !== DEVICE.id);
        return { removed: 1 };
      }
      if (url === "/api/push/test") return { sent: 1, failed: 0, removed: 0 };
      if (url === "/api/push/prefs") {
        state.prefs = body;
        return body;
      }
      if (url.startsWith("/api/push/devices/")) {
        const id = decodeURIComponent(url.split("/").pop()!);
        state.devices = state.devices.filter((d) => d.id !== id);
        return { removed: 1 };
      }
      return state;
    },
  });
  return { fetchMock, calls, state };
}

afterEach(() => {
  vi.unstubAllGlobals();
  setDeviceId(null);
  for (const k of ["userAgent", "standalone", "serviceWorker"])
    delete (navigator as unknown as Record<string, unknown>)[k];
});

describe("휴대폰 알림 카드 — 켤 수 없는 상태", () => {
  it("서버에 VAPID 키가 없으면 이유를 보여준다", async () => {
    setupBrowser();
    mockPushApi(status({ available: false, reason: "VAPID 키가 설정되지 않았어요", public_key: null }));
    renderWithProviders(<PushSettings />);
    expect(await screen.findByText(/VAPID 키가 설정되지 않았어요/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "알림 켜기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "받을 알림" })).not.toBeInTheDocument();
  });

  it("HTTPS 가 아니면 HTTPS(Tailscale) 주소로 접속하라고 안내한다", async () => {
    setupBrowser({ secure: false, push: false });
    mockPushApi(status());
    renderWithProviders(<PushSettings />);
    expect(await screen.findByText(/HTTPS 주소/)).toBeInTheDocument();
    expect(screen.getByText(/Tailscale/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "알림 켜기" })).not.toBeInTheDocument();
  });

  it("iPhone Safari 탭에서는 '홈 화면에 추가' 방법을 안내한다", async () => {
    setupBrowser({ standalone: false, push: false });
    mockPushApi(status());
    renderWithProviders(<PushSettings />);
    expect(await screen.findByText("'홈 화면에 추가'")).toBeInTheDocument();
    expect(screen.getByText("공유 버튼")).toBeInTheDocument();
    expect(screen.getByText(/홈 화면에 추가한 앱에서만/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "알림 켜기" })).not.toBeInTheDocument();
  });

  it("푸시 기능이 없는 브라우저", async () => {
    setupBrowser({
      ua: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      standalone: false,
      push: false,
    });
    mockPushApi(status());
    renderWithProviders(<PushSettings />);
    expect(await screen.findByText("이 브라우저는 알림을 지원하지 않아요.")).toBeInTheDocument();
  });

  it("알림 권한이 거부돼 있으면 설정 > 알림에서 허용하라고 안내한다", async () => {
    setupBrowser({ permission: "denied" });
    mockPushApi(status());
    renderWithProviders(<PushSettings />);
    expect(await screen.findByText(/설정 > 알림/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "알림 켜기" })).not.toBeInTheDocument();
  });
});

describe("휴대폰 알림 카드 — 켜기·끄기", () => {
  it("알림 켜기: 권한 요청 → 구독(userVisibleOnly) → 서버 등록, 그리고 '이 기기' 표시", async () => {
    const b = setupBrowser();
    const { calls } = mockPushApi(status());
    renderWithProviders(<PushSettings />);
    const on = await screen.findByRole("button", { name: "알림 켜기" });
    fireEvent.click(on);
    expect(b.notification.requestPermission).toHaveBeenCalledTimes(1); // 클릭 처리 안에서 바로
    expect(await screen.findByText("이 기기에서 알림을 받고 있어요 ✓")).toBeInTheDocument();

    const [opts] = b.pushManager.subscribe.mock.calls[0];
    expect(opts.userVisibleOnly).toBe(true);
    expect(Array.from(opts.applicationServerKey as Uint8Array)).toEqual([4, 1, 2, 3, 4]);
    const sub = calls.find((c) => c.url === "/api/push/subscribe");
    expect(sub?.method).toBe("POST");
    expect(sub?.body).toEqual({
      endpoint: ENDPOINT,
      keys: { p256dh: "p256-key", auth: "auth-key" },
      label: "iPhone · Safari",
    });
    const list = await screen.findByRole("list");
    expect(within(list).getByText("이 기기")).toBeInTheDocument();
  });

  it("권한 창에서 거부하면 서버에 등록하지 않고 안내로 바뀐다", async () => {
    const b = setupBrowser({ grant: "denied" });
    const { calls } = mockPushApi(status());
    renderWithProviders(<PushSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "알림 켜기" }));
    expect(await screen.findByText(/설정 > 알림/)).toBeInTheDocument();
    expect(b.pushManager.subscribe).not.toHaveBeenCalled();
    expect(calls.some((c) => c.url === "/api/push/subscribe")).toBe(false);
  });

  it("켜진 기기: 테스트 알림은 이 기기 endpoint 로, 끄기는 구독 해제 + 서버 해제", async () => {
    const b = setupBrowser({ subscribed: true });
    setDeviceId(DEVICE.id);
    const { calls } = mockPushApi(status({ devices: [DEVICE, OTHER] }));
    renderWithProviders(<PushSettings />);
    fireEvent.click(await screen.findByRole("button", { name: "테스트 알림 보내기" }));
    expect(await screen.findByText(/보냈어요 ✓ \(1대\)/)).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/push/test")?.body).toEqual({ endpoint: ENDPOINT });

    const sub = b.sub()!;
    fireEvent.click(screen.getByRole("button", { name: "이 기기 알림 끄기" }));
    expect(await screen.findByRole("button", { name: "알림 켜기" })).toBeInTheDocument();
    expect(sub.unsubscribe).toHaveBeenCalled();
    const off = calls.find((c) => c.url === "/api/push/unsubscribe");
    expect(off?.method).toBe("POST");
    expect(off?.body).toEqual({ endpoint: ENDPOINT });
  });

  it("브라우저 구독은 있어도 서버 목록에 없으면 꺼진 것으로 보고 다시 켤 수 있다", async () => {
    setupBrowser({ subscribed: true });
    setDeviceId(DEVICE.id);
    mockPushApi(status({ devices: [OTHER] }));
    renderWithProviders(<PushSettings />);
    expect(await screen.findByRole("button", { name: "알림 켜기" })).toBeInTheDocument();
    expect(screen.queryByText("이 기기")).not.toBeInTheDocument();
  });
});

describe("휴대폰 알림 카드 — 종류·기기 목록", () => {
  it("알림 종류 토글은 전체 설정을 PUT 한다", async () => {
    setupBrowser();
    const { calls } = mockPushApi(status());
    renderWithProviders(<PushSettings />);
    const agent = await screen.findByRole("checkbox", { name: /에이전트 판단 완료·리포트/ });
    await waitFor(() => expect(agent).not.toBeDisabled());
    expect(agent).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /체결/ })).toBeChecked();
    fireEvent.click(agent);
    await waitFor(() => expect(calls.some((c) => c.url === "/api/push/prefs")).toBe(true));
    const put = calls.find((c) => c.url === "/api/push/prefs")!;
    expect(put.method).toBe("PUT");
    expect(put.body).toEqual({ fills: true, risk: true, alerts: true, agent: true });
    await waitFor(() => expect(agent).toBeChecked());
  });

  it("다른 기기를 목록에서 삭제한다 (DELETE)", async () => {
    setupBrowser();
    const { calls } = mockPushApi(status({ devices: [OTHER] }));
    renderWithProviders(<PushSettings />);
    expect(await screen.findByText("알림 받는 기기 1대")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Mac · Chrome 삭제" }));
    await waitFor(() => expect(screen.queryByText("Mac · Chrome")).not.toBeInTheDocument());
    const d = calls.find((c) => c.method === "DELETE");
    expect(d?.url).toBe("/api/push/devices/dev-2");
  });
});
