import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  deviceLabel,
  isIOS,
  isStandalone,
  pushErrorText,
  pushSupport,
  registerServiceWorker,
  subscribePush,
  subscriptionBody,
  urlBase64ToUint8Array,
  type PushEnv,
} from "../push";

const IPHONE_SAFARI =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
const IPHONE_CHROME =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0 Mobile/15E148 Safari/604.1";
const MAC_SAFARI =
  "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
const ANDROID_CHROME =
  "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36";
const WINDOWS_EDGE =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0";

/** 바이트 → base64url (패딩 없음) */
function toB64Url(bytes: number[]): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

afterEach(() => {
  vi.unstubAllGlobals();
  delete (navigator as { standalone?: boolean }).standalone;
  delete (navigator as { serviceWorker?: unknown }).serviceWorker;
});

describe("VAPID 공개키 변환", () => {
  it("base64url(패딩 없음, -/_ 포함)을 원래 바이트로 되돌린다", () => {
    const bytes = [4, 251, 255, 254, 62, 63, 0, 128, 7];
    const key = toB64Url(bytes);
    expect(key).toMatch(/[-_]/);
    expect(key).not.toContain("=");
    expect(Array.from(urlBase64ToUint8Array(key))).toEqual(bytes);
  });

  it("65바이트 키 길이를 지킨다", () => {
    const bytes = Array.from({ length: 65 }, (_, i) => (i * 37) % 256);
    expect(urlBase64ToUint8Array(toB64Url(bytes))).toHaveLength(65);
  });
});

describe("기기 판별", () => {
  it("iPhone·iPad(데스크톱 모드 포함)만 iOS 로 본다", () => {
    expect(isIOS(IPHONE_SAFARI)).toBe(true);
    expect(isIOS(MAC_SAFARI, "MacIntel", 5)).toBe(true); // iPadOS
    expect(isIOS(MAC_SAFARI, "MacIntel", 0)).toBe(false);
    expect(isIOS(ANDROID_CHROME, "Linux armv8l", 5)).toBe(false);
  });

  it("홈 화면 앱(standalone) 여부", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: false, media: q }));
    expect(isStandalone()).toBe(false);
    Object.defineProperty(navigator, "standalone", { value: true, configurable: true });
    expect(isStandalone()).toBe(true);
    delete (navigator as { standalone?: boolean }).standalone;
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q === "(display-mode: standalone)", media: q }));
    expect(isStandalone()).toBe(true);
  });

  it("기기 이름", () => {
    expect(deviceLabel(IPHONE_SAFARI)).toBe("iPhone · Safari");
    expect(deviceLabel(IPHONE_CHROME)).toBe("iPhone · Chrome");
    expect(deviceLabel(MAC_SAFARI, "MacIntel", 5)).toBe("iPad · Safari");
    expect(deviceLabel(MAC_SAFARI)).toBe("Mac · Safari");
    expect(deviceLabel(ANDROID_CHROME)).toBe("Android · Chrome");
    expect(deviceLabel(WINDOWS_EDGE)).toBe("Windows · Edge");
  });
});

describe("알림 지원 여부", () => {
  const ok: PushEnv = {
    secure: true,
    serviceWorker: true,
    pushManager: true,
    notification: true,
    ios: false,
    standalone: false,
  };
  it("HTTPS → iOS 홈 화면 → 브라우저 기능 순으로 확인한다", () => {
    expect(pushSupport(ok)).toBe("ok");
    expect(pushSupport({ ...ok, secure: false })).toBe("insecure");
    // HTTP 에서는 API 도 없지만 HTTPS 안내가 먼저
    expect(pushSupport({ ...ok, secure: false, serviceWorker: false, pushManager: false })).toBe("insecure");
    // iOS Safari 탭: 푸시 API 가 숨겨져 있어도 '홈 화면에 추가' 안내
    expect(pushSupport({ ...ok, ios: true, pushManager: false, notification: false })).toBe("ios-install");
    expect(pushSupport({ ...ok, ios: true, standalone: true })).toBe("ok");
    expect(pushSupport({ ...ok, ios: true, standalone: true, pushManager: false })).toBe("unsupported");
    expect(pushSupport({ ...ok, notification: false })).toBe("unsupported");
    expect(pushSupport({ ...ok, serviceWorker: false })).toBe("unsupported");
  });
});

describe("구독", () => {
  it("구독 정보에서 endpoint·키를 꺼내고, 빠져 있으면 오류", () => {
    const sub = (json: PushSubscriptionJSON) => ({ toJSON: () => json }) as unknown as PushSubscription;
    expect(
      subscriptionBody(sub({ endpoint: "https://push.example.com/a", keys: { p256dh: "P", auth: "A" } })),
    ).toEqual({ endpoint: "https://push.example.com/a", keys: { p256dh: "P", auth: "A" } });
    expect(() => subscriptionBody(sub({ endpoint: "https://push.example.com/a" }))).toThrow(/구독 정보/);
  });

  it("서버 키가 바뀌었으면 기존 구독을 끊고 새 키로 구독한다 (userVisibleOnly)", async () => {
    const oldSub = {
      endpoint: "https://push.example.com/old",
      options: { applicationServerKey: new Uint8Array([9, 9, 9]).buffer },
      unsubscribe: vi.fn(async () => true),
    };
    const newSub = {
      endpoint: "https://push.example.com/new",
      toJSON: () => ({ endpoint: "https://push.example.com/new", keys: { p256dh: "P", auth: "A" } }),
    };
    const pushManager = {
      getSubscription: vi.fn(async () => oldSub),
      subscribe: vi.fn(async () => newSub),
    };
    const reg = { pushManager };
    Object.defineProperty(navigator, "serviceWorker", {
      value: { getRegistration: vi.fn(async () => reg), register: vi.fn(), ready: Promise.resolve(reg) },
      configurable: true,
    });
    const body = await subscribePush(toB64Url([4, 1, 2, 3]));
    expect(oldSub.unsubscribe).toHaveBeenCalled();
    const opts = pushManager.subscribe.mock.calls[0] as unknown as [PushSubscriptionOptionsInit];
    expect(opts[0].userVisibleOnly).toBe(true);
    expect(Array.from(opts[0].applicationServerKey as Uint8Array)).toEqual([4, 1, 2, 3]);
    expect(body.endpoint).toBe("https://push.example.com/new");
  });

  it("권한 거부 오류는 한국어로 보여준다", () => {
    expect(pushErrorText(new DOMException("denied", "NotAllowedError"))).toBe(
      "알림 권한이 허용되지 않았어요",
    );
    expect(pushErrorText(new Error("boom"))).toBe("boom");
  });
});

describe("서비스 워커 등록", () => {
  it("지원하면 /sw.js 를 / 범위로 등록하고, 없으면 조용히 넘어간다", () => {
    expect(() => registerServiceWorker()).not.toThrow(); // jsdom: serviceWorker 없음
    const register = vi.fn(async () => ({}));
    Object.defineProperty(navigator, "serviceWorker", { value: { register }, configurable: true });
    registerServiceWorker(); // 테스트 문서는 이미 load 완료 상태
    expect(register).toHaveBeenCalledWith("/sw.js", { scope: "/" });
  });
});

// ---- public/sw.js: 가짜 self 위에서 실행해 이벤트 처리만 확인 ----
type Listener = (e: unknown) => void;

function loadServiceWorker(windows: { url: string; focus: () => Promise<unknown>; navigate?: unknown }[]) {
  const code = readFileSync(resolve(process.cwd(), "public/sw.js"), "utf8");
  const listeners: Record<string, Listener> = {};
  const self = {
    addEventListener: (type: string, fn: Listener) => (listeners[type] = fn),
    skipWaiting: vi.fn(),
    location: { origin: "https://app.test" },
    registration: { showNotification: vi.fn(async () => undefined) },
    clients: {
      claim: vi.fn(async () => undefined),
      matchAll: vi.fn(async () => windows),
      openWindow: vi.fn(async () => null),
    },
  };
  new Function("self", code)(self);
  const fire = async (type: string, event: Record<string, unknown>) => {
    const waits: Promise<unknown>[] = [];
    listeners[type]({ ...event, waitUntil: (p: Promise<unknown>) => waits.push(p) });
    await Promise.all(waits);
  };
  return { self, fire };
}

describe("서비스 워커 (public/sw.js)", () => {
  it("JSON 알림을 제목·본문·태그·이동 경로로 띄운다", async () => {
    const { self, fire } = loadServiceWorker([]);
    const payload = { title: "체결", body: "삼성전자 10주 매수", url: "/portfolio", tag: "fill-1" };
    await fire("push", { data: { json: () => payload, text: () => JSON.stringify(payload) } });
    expect(self.registration.showNotification).toHaveBeenCalledWith("체결", {
      body: "삼성전자 10주 매수",
      tag: "fill-1",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      data: { url: "/portfolio" },
    });
  });

  it("JSON 이 아니면 글자를 본문으로, 외부 주소는 홈으로", async () => {
    const { self, fire } = loadServiceWorker([]);
    await fire("push", {
      data: {
        json: () => {
          throw new SyntaxError("not json");
        },
        text: () => "그냥 글자",
      },
    });
    expect(self.registration.showNotification).toHaveBeenLastCalledWith(
      "AI 펀드매니저",
      expect.objectContaining({ body: "그냥 글자", data: { url: "/" } }),
    );
    const evil = { title: "t", body: "b", url: "https://evil.example.com" };
    await fire("push", { data: { json: () => evil, text: () => "" } });
    expect(self.registration.showNotification).toHaveBeenLastCalledWith(
      "t",
      expect.objectContaining({ data: { url: "/" } }),
    );
  });

  it("알림을 누르면 열린 창을 앞으로 가져와 해당 화면으로 이동한다", async () => {
    const win = { url: "https://app.test/", focus: vi.fn(), navigate: vi.fn(async () => null) };
    win.focus.mockResolvedValue(win);
    const { self, fire } = loadServiceWorker([win]);
    const close = vi.fn();
    await fire("notificationclick", { notification: { close, data: { url: "/room" } } });
    expect(close).toHaveBeenCalled();
    expect(win.focus).toHaveBeenCalled();
    expect(win.navigate).toHaveBeenCalledWith("https://app.test/room");
    expect(self.clients.openWindow).not.toHaveBeenCalled();
  });

  it("열린 창이 없으면 새로 연다", async () => {
    const { self, fire } = loadServiceWorker([]);
    await fire("notificationclick", { notification: { close: vi.fn(), data: { url: "/logs" } } });
    expect(self.clients.openWindow).toHaveBeenCalledWith("https://app.test/logs");
  });
});
