/* 웹 푸시 전용 서비스 워커.
 * 캐시·오프라인 처리는 일부러 하지 않는다 — 화면은 항상 서버의 최신 상태를 그대로 보여 준다.
 * 서버가 보내는 내용: JSON { title, body, url, tag? } (url 은 '/portfolio' 같은 앱 안 경로)
 */
const APP_NAME = "AI 펀드매니저";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

function readPayload(data) {
  if (!data) return {};
  try {
    const parsed = data.json();
    if (parsed && typeof parsed === "object") return parsed;
    return { body: String(parsed) };
  } catch {
    // JSON 이 아니면 받은 글자를 그대로 본문으로
    return { body: data.text() };
  }
}

// 앱 안 경로만 연다 (다른 사이트 주소·'//host' 형태는 홈으로)
function appPath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : "/";
}

self.addEventListener("push", (event) => {
  const p = readPayload(event.data);
  const options = {
    body: typeof p.body === "string" && p.body ? p.body : "새 알림이 있어요",
    icon: "/icon-192.png",
    badge: "/icon-192.png",
    data: { url: appPath(p.url) },
  };
  if (typeof p.tag === "string" && p.tag) options.tag = p.tag;
  const title = typeof p.title === "string" && p.title ? p.title : APP_NAME;
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(
    appPath(event.notification.data && event.notification.data.url),
    self.location.origin,
  ).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const client = windows.find((c) => new URL(c.url).origin === self.location.origin);
      if (client) {
        try {
          const focused = (await client.focus()) || client;
          if (focused.url !== target && "navigate" in focused) await focused.navigate(target);
          return;
        } catch {
          /* 이 창을 다룰 수 없으면 새 창으로 */
        }
      }
      await self.clients.openWindow(target);
    })(),
  );
});
