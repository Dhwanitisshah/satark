// Behavioural checks for frontend/sw.js, run with plain Node (no dependencies):
//   node tests/sw_check.js
// The worker is loaded into a stub service-worker scope with a fake Cache Storage and a fake fetch.
// tests/test_pwa.py runs it under pytest when Node is installed.
const fs = require("fs"), vm = require("vm"), path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "frontend", "sw.js"), "utf8");

const ORIGIN = "https://satark.test";
const resp = (body, ok = true) => ({ ok, body, clone() { return { ...this }; } });

function makeWorker(fetchImpl, { cachedShell = null, fastTimer = true } = {}) {
  const listeners = {}, stores = new Map(), puts = [], added = [], deleted = [];
  let skipped = false, claimed = false;
  const makeCache = name => {
    const m = stores.get(name) || new Map(); stores.set(name, m);
    return {
      async match(k) { return m.get(typeof k === "string" ? k : new URL(k.url).pathname); },
      async put(k, v) { puts.push(k); m.set(k, v); },
      async addAll(urls) { added.push(...urls); for (const u of urls) m.set(u, resp("cached:" + u)); },
    };
  };
  const caches = {
    open: async n => makeCache(n),
    keys: async () => [...stores.keys()],
    delete: async n => { deleted.push(n); stores.delete(n); return true; },
    match: async k => { for (const m of stores.values()) if (m.has(k)) return m.get(k); },
  };
  if (cachedShell) { const c = makeCache("satark-shell-v1"); c.put("/", cachedShell); puts.length = 0; }
  const self = {
    location: { origin: ORIGIN }, addEventListener: (ev, fn) => { listeners[ev] = fn; },
    skipWaiting: () => { skipped = true; }, clients: { claim: async () => { claimed = true; } },
  };
  const timer = fastTimer ? (fn, ms) => setTimeout(fn, ms >= 1000 ? 15 : ms) : setTimeout;
  vm.runInNewContext(src, { self, caches, fetch: fetchImpl, URL, Promise, setTimeout: timer, console });
  const dispatch = async (url, { method = "GET", mode = "cors", waitPending = true } = {}) => {
    let answered, pending = [];
    const event = { request: { url: ORIGIN + url, method, mode }, respondWith: p => { answered = Promise.resolve(p); }, waitUntil: p => pending.push(p) };
    listeners.fetch(event);
    const out = answered ? await answered : undefined;
    if (waitPending) await Promise.all(pending);
    return { intercepted: !!answered, out };
  };
  return { listeners, stores, puts, added, deleted, dispatch, flags: () => ({ skipped, claimed }) };
}

let failed = 0; const check = (name, cond) => { console.log((cond ? "PASS " : "FAIL ") + name); if (!cond) failed++; };
const live = () => Promise.resolve(resp("network"));

(async () => {
  // Install and activate
  { const w = makeWorker(live);
    const waits = []; w.listeners.install({ waitUntil: p => waits.push(p) }); await Promise.all(waits);
    check("install caches the app shell, manifest and icons", w.added.join() === "/,/manifest.json,/static/icons/icon-192.png,/static/icons/icon-512.png");
    check("install activates the new worker straight away", w.flags().skipped === true);
    check("nothing from the API is precached", !w.added.some(u => u.startsWith("/api"))); }
  { const w = makeWorker(live); await w.listeners.install({ waitUntil: () => {} });
    w.stores.set("satark-shell-v0", new Map()); w.stores.set("something-else", new Map());
    const waits = []; w.listeners.activate({ waitUntil: p => waits.push(p) }); await Promise.all(waits);
    check("activate removes old caches and keeps the current one", w.deleted.sort().join() === "satark-shell-v0,something-else" && w.flags().claimed); }

  // API and other traffic is never touched
  { const calls = []; const w = makeWorker(u => { calls.push(u); return live(); });
    for (const [url, opts] of [["/api/check", { method: "POST" }], ["/api/check", {}], ["/api/health", {}], ["/api/check?x=1", { mode: "navigate" }]]) {
      const r = await w.dispatch(url, opts);
      check(`${opts.method || "GET"} ${url}: not intercepted`, !r.intercepted); }
    check("no API traffic went through the worker's fetch or cache", calls.length === 0 && w.puts.length === 0); }
  { const w = makeWorker(live);
    check("POST to the page itself: not intercepted", !(await w.dispatch("/", { method: "POST", mode: "navigate" })).intercepted);
    check("other same-origin files: not intercepted", !(await w.dispatch("/static/index.js")).intercepted);
    const cross = { request: { url: "https://fonts.googleapis.com/css2", method: "GET", mode: "cors" }, respondWith() { throw new Error("intercepted"); }, waitUntil() {} };
    let threw = false; try { w.listeners.fetch(cross); } catch { threw = true; }
    check("other origins (fonts): not intercepted", !threw); }

  // The page: network first, cached copy as the fallback
  { const w = makeWorker(live, { cachedShell: resp("old page") });
    const r = await w.dispatch("/", { mode: "navigate" });
    check("online: the fresh page is served", r.out.body === "network");
    check("online: the cached shell is refreshed under '/'", w.puts.join() === "/"); }
  { const w = makeWorker(live, { cachedShell: resp("old page") });
    const r = await w.dispatch("/?title=t&text=" + encodeURIComponent("secret message") + "&url=u", { mode: "navigate" });
    check("a share URL gets the page", r.out.body === "network");
    check("the shared message is never used as a cache key", w.puts.join() === "/" && ![...w.stores.get("satark-shell-v1").keys()].some(k => k.includes("secret"))); }
  { const w = makeWorker(() => Promise.reject(new TypeError("offline")), { cachedShell: resp("old page") });
    const r = await w.dispatch("/", { mode: "navigate" });
    check("offline: the cached shell is shown", r.out.body === "old page"); }
  { const w = makeWorker(() => Promise.resolve(resp("Bad gateway", false)), { cachedShell: resp("old page") });
    const r = await w.dispatch("/", { mode: "navigate" });
    check("server error: the cached shell is shown instead of the error", r.out.body === "old page");
    check("an error page is never cached", w.puts.length === 0); }
  { let release; const hang = new Promise(r => release = r);
    const w = makeWorker(() => hang.then(() => resp("late")), { cachedShell: resp("old page") });
    const r = await w.dispatch("/", { mode: "navigate", waitPending: false }).then(x => { release(); return x; });
    check("sleeping server: the cached shell appears after the wait instead of a blank page", r.out.body === "old page"); }
  { const w = makeWorker(live);
    const r = await w.dispatch("/", { mode: "navigate" });
    check("first visit with nothing cached: straight to the network", r.out.body === "network"); }
  { const w = makeWorker(() => Promise.reject(new TypeError("offline")));
    let failedAsExpected = false;
    try { await w.dispatch("/", { mode: "navigate" }); } catch { failedAsExpected = true; }
    check("offline with nothing cached: the browser's own error, not a fake page", failedAsExpected); }

  // Manifest and icons: cache first
  { const w = makeWorker(() => { throw new Error("should not hit the network"); });
    await w.listeners.install({ waitUntil: () => {} });
    const r = await w.dispatch("/manifest.json");
    check("manifest comes from the cache", r.intercepted && r.out.body === "cached:/manifest.json"); }

  console.log(failed ? `${failed} FAILED` : "all service-worker checks passed"); process.exit(failed ? 1 : 0);
})();
