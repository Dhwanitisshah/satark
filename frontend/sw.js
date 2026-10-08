// Satark service worker. It exists so the site can be installed and used as an Android share target.
// It caches the app shell (the page, the manifest, the icons) and nothing else: API calls and every
// non-GET request go straight to the network, and the worker never stores a verdict or a message.
const CACHE = "satark-shell-v1";
const SHELL = "/";
const ASSETS = [SHELL, "/manifest.json", "/static/icons/icon-192.png", "/static/icons/icon-512.png"];
const FRESH_WAIT_MS = 4000;   // how long to wait for the network before showing the cached page

self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// Page: network first, so a deploy shows up on the next visit. If the network is offline or slower than
// FRESH_WAIT_MS (a sleeping free-tier server), show the cached page; the page itself explains the wait.
async function page(event) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(SHELL);
  const fresh = fetch(event.request).then(res => {
    if (res.ok) cache.put(SHELL, res.clone());   // always stored under "/", never under "/?text=…"
    return res;
  });
  if (!cached) return fresh;
  event.waitUntil(fresh.catch(() => {}));        // let the cache refresh finish even if we answered early
  const slow = new Promise(resolve => setTimeout(() => resolve(cached), FRESH_WAIT_MS));
  return Promise.race([fresh.then(res => (res.ok ? res : cached), () => cached), slow]);
}

self.addEventListener("fetch", event => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;   // fonts and other origins: the browser handles them
  if (url.pathname.startsWith("/api/")) return;      // never cache or intercept API calls
  if (req.mode === "navigate" && url.pathname === SHELL) {
    event.respondWith(page(event));
  } else if (ASSETS.slice(1).includes(url.pathname)) {   // manifest and icons: cache first (bump CACHE to change)
    event.respondWith(caches.match(url.pathname).then(hit => hit || fetch(req)));
  }
});
