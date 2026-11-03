// 웹 푸시(휴대폰 알림) 브라우저 쪽 도우미. 서버 API 호출은 설정 화면이, 브라우저 기능은 여기서 다룬다.
// iPhone(iOS 16.4+)은 '홈 화면에 추가'한 앱에서만, HTTPS 에서만, 사용자가 누른 버튼에서만 권한을 물을 수 있다.
import { errorText } from "./api/client";

export const SW_URL = "/sw.js";
export const SW_SCOPE = "/";

/** ok: 켤 수 있음 · insecure: HTTPS 아님 · ios-install: iPhone 인데 홈 화면 앱이 아님 · unsupported: 브라우저 미지원 */
export type PushSupport = "ok" | "insecure" | "ios-install" | "unsupported";

export interface PushEnv {
  secure: boolean;
  serviceWorker: boolean;
  pushManager: boolean;
  notification: boolean;
  ios: boolean;
  standalone: boolean;
}

export interface PushSubscriptionBody {
  endpoint: string;
  keys: { p256dh: string; auth: string };
}

/** iPhone·iPad (iPadOS 는 Mac 으로 자신을 소개하므로 터치 지원으로 구분) */
export function isIOS(ua: string, platform = "", maxTouchPoints = 0): boolean {
  return /iPad|iPhone|iPod/.test(ua) || (platform === "MacIntel" && maxTouchPoints > 1);
}

/** 홈 화면 아이콘으로 연 앱(standalone)인지 */
export function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  if ((window.navigator as Navigator & { standalone?: boolean }).standalone === true) return true;
  return typeof window.matchMedia === "function" && window.matchMedia("(display-mode: standalone)").matches;
}

export function readEnv(): PushEnv {
  const nav = window.navigator;
  return {
    secure: window.isSecureContext === true,
    serviceWorker: "serviceWorker" in nav,
    pushManager: "PushManager" in window,
    notification: "Notification" in window,
    ios: isIOS(nav.userAgent, nav.platform, nav.maxTouchPoints),
    standalone: isStandalone(),
  };
}

export function pushSupport(env: PushEnv = readEnv()): PushSupport {
  // HTTP 에서는 서비스 워커·푸시가 아예 없다 — '미지원'보다 HTTPS 안내가 먼저
  if (!env.secure) return "insecure";
  // iOS Safari 탭에서는 푸시 API 가 숨겨져 있다 — 홈 화면 앱으로 열어야 생긴다
  if (env.ios && !env.standalone) return "ios-install";
  if (!env.serviceWorker || !env.pushManager || !env.notification) return "unsupported";
  return "ok";
}

/** 알림 권한 상태 (브라우저가 지원하지 않으면 'unsupported') */
export function notificationPermission(): NotificationPermission | "unsupported" {
  return typeof window !== "undefined" && "Notification" in window ? Notification.permission : "unsupported";
}

/** 반드시 버튼 클릭 처리 안에서 곧바로(다른 await 전에) 불러야 iOS 가 권한 창을 띄운다 */
export function requestPermission(): Promise<NotificationPermission> {
  if (!("Notification" in window)) return Promise.resolve("denied");
  return Notification.requestPermission();
}

/** VAPID 공개키(base64url) → applicationServerKey 바이트 */
export function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padded = base64.trim() + "=".repeat((4 - (base64.trim().length % 4)) % 4);
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

/** 기기 목록에 보일 이름. 예: "iPhone · Safari" */
export function deviceLabel(ua: string, platform = "", maxTouchPoints = 0): string {
  const device = /iPhone|iPod/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua) || (platform === "MacIntel" && maxTouchPoints > 1)
      ? "iPad"
      : /Android/.test(ua)
        ? "Android"
        : /Windows/.test(ua)
          ? "Windows"
          : /Macintosh|Mac OS X/.test(ua)
            ? "Mac"
            : /Linux/.test(ua)
              ? "Linux"
              : "기기";
  const browser = /SamsungBrowser/.test(ua)
    ? "Samsung Internet"
    : /Edg\//.test(ua)
      ? "Edge"
      : /Firefox|FxiOS/.test(ua)
        ? "Firefox"
        : /Chrome|CriOS/.test(ua)
          ? "Chrome"
          : /Safari/.test(ua)
            ? "Safari"
            : "브라우저";
  return `${device} · ${browser}`;
}

/** 앱 시작 시 서비스 워커 등록 (지원하지 않거나 실패해도 앱은 그대로) */
export function registerServiceWorker(): void {
  if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
  const register = () => {
    navigator.serviceWorker.register(SW_URL, { scope: SW_SCOPE }).catch(() => {
      /* 등록 실패 — 알림만 못 쓸 뿐 */
    });
  };
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, { once: true });
}

/** 활성화된 서비스 워커 등록 정보 (없으면 지금 등록) */
async function activeRegistration(): Promise<ServiceWorkerRegistration> {
  const sw = navigator.serviceWorker;
  const reg = await sw.getRegistration(SW_SCOPE);
  if (!reg) await sw.register(SW_URL, { scope: SW_SCOPE });
  return sw.ready;
}

/** 이 기기의 현재 구독 (없거나 확인할 수 없으면 null) */
export async function currentSubscription(): Promise<PushSubscription | null> {
  try {
    if (!("serviceWorker" in navigator)) return null;
    const reg = await navigator.serviceWorker.getRegistration(SW_SCOPE);
    return reg ? await reg.pushManager.getSubscription() : null;
  } catch {
    return null;
  }
}

function sameKey(a: ArrayBuffer | null | undefined, b: Uint8Array): boolean {
  if (!a) return false;
  const x = new Uint8Array(a);
  return x.length === b.length && x.every((v, i) => v === b[i]);
}

export function subscriptionBody(sub: PushSubscription): PushSubscriptionBody {
  const json = sub.toJSON();
  const p256dh = json.keys?.p256dh;
  const auth = json.keys?.auth;
  if (!json.endpoint || !p256dh || !auth) throw new Error("구독 정보를 읽지 못했어요. 다시 시도해 주세요");
  return { endpoint: json.endpoint, keys: { p256dh, auth } };
}

/** 푸시 구독 (권한은 먼저 requestPermission 으로 받아 둔다). 서버 키가 바뀌었으면 새로 구독한다 */
export async function subscribePush(publicKey: string): Promise<PushSubscriptionBody> {
  const key = urlBase64ToUint8Array(publicKey);
  const reg = await activeRegistration();
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub.options?.applicationServerKey, key)) {
    await sub.unsubscribe();
    sub = null;
  }
  sub ??= await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
  return subscriptionBody(sub);
}

/** 이 기기 구독 해제. 해제한 endpoint 를 돌려준다 (구독이 없었으면 null) */
export async function unsubscribePush(): Promise<string | null> {
  const sub = await currentSubscription();
  if (!sub) return null;
  const endpoint = sub.endpoint;
  await sub.unsubscribe();
  return endpoint;
}

export function pushErrorText(e: unknown): string {
  const name = e instanceof Error || e instanceof DOMException ? e.name : "";
  if (name === "NotAllowedError") return "알림 권한이 허용되지 않았어요";
  if (name === "AbortError") return "푸시 서비스에 연결하지 못했어요. 잠시 뒤 다시 시도해 주세요";
  if (name === "InvalidStateError" || name === "InvalidAccessError")
    return "알림 등록에 실패했어요. 앱을 완전히 닫았다가 다시 열어 주세요";
  return errorText(e);
}

// 서버가 돌려준 이 기기의 id — 기기 목록에서 '이 기기'를 표시하는 데 쓴다 (저장소가 막혀 있으면 메모리)
const DEVICE_KEY = "push.deviceId";
let memoryDeviceId: string | null = null;

export function getDeviceId(): string | null {
  try {
    return window.localStorage.getItem(DEVICE_KEY) ?? memoryDeviceId;
  } catch {
    return memoryDeviceId;
  }
}

export function setDeviceId(id: string | null): void {
  memoryDeviceId = id;
  try {
    if (id) window.localStorage.setItem(DEVICE_KEY, id);
    else window.localStorage.removeItem(DEVICE_KEY);
  } catch {
    /* 저장소 사용 불가 — 메모리에만 */
  }
}
