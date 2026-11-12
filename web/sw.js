const CACHE = "employee-portal-shell-v8";
const SHELL = ["/", "/styles.css", "/app.js", "/admin.js", "/notifications.js", "/work.js", "/presence.js", "/manifest.webmanifest", "/icons/icon.svg"];
self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});
self.addEventListener("activate", (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))));
  self.clients.claim();
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/") || event.request.method !== "GET") return;
  event.respondWith(fetch(event.request).then((response) => {
    const copy = response.clone();
    caches.open(CACHE).then((cache) => cache.put(event.request, copy));
    return response;
  }).catch(() => caches.match(event.request).then((cached) => cached || caches.match("/"))));
});


self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const data = event.notification.data || {};
    const message = { type: data.notificationId ? "OPEN_NOTIFICATION" : "OPEN_NOTIFICATIONS", notificationId: data.notificationId || null, notificationType: data.notificationType || null, requestId: data.requestId || null, employeeId: data.employeeId || null };
    for (const client of windows) {
      if ("focus" in client) {
        await client.focus();
        client.postMessage(message);
        return;
      }
    }
    if (self.clients.openWindow) {
      const destination = new URL(data.url || "/", self.location.origin);
      if (data.notificationId) destination.searchParams.set("openNotification", String(data.notificationId));
      await self.clients.openWindow(destination.href);
    }
  })());
});


self.addEventListener("push", (event) => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) {}
  const title = String(payload.title || "تنبيه جديد").slice(0, 160);
  const body = String(payload.body || "لديك إشعار جديد.").slice(0, 1000);
  const url = typeof payload.url === "string" && payload.url.startsWith("/") ? payload.url : "/";
  event.waitUntil(self.registration.showNotification(title, {
    body,
    icon: "/icons/icon.svg",
    badge: "/icons/icon.svg",
    tag: String(payload.tag || "employee-portal-push").slice(0, 120),
    renotify: false,
    data: { url, notificationId: payload.notification_id || null, notificationType: payload.notification_type || null, requestId: payload.request_id || null, employeeId: payload.employee_id || null }
  }));
});
