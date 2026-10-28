const CACHE = "reminders-v4";
const SHELL = ["./", "index.html", "manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

// ط§ظ„ظ†طھ ط£ظˆظ„ ط¹ط´ط§ظ† ط§ظ„طھط­ط¯ظٹط«ط§طھ طھظˆطµظ„طŒ ظˆط§ظ„ظ†ط³ط®ط© ط§ظ„ظ…ط­ظپظˆط¸ط© ظ„ظˆ ظ…ط§ ظپظٹظ‡ ظ†طھ
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.hostname.endsWith("supabase.co")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok && (url.origin === location.origin || url.hostname === "cdn.jsdelivr.net")) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch { d = { title: e.data?.text() || "طھط°ظƒظٹط±" }; }
  e.waitUntil(self.registration.showNotification(d.head || "âڈ° طھط°ظƒظٹط±", {
    body: d.title + (d.when ? `\n${d.when}` : ""),
    tag: d.tag,
    icon: "icon-192.png",
    badge: "icon-192.png",
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  e.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const c of list) if ("focus" in c) return c.focus();
      return self.clients.openWindow("./");
    })
  );
});
