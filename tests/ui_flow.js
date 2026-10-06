// Behavioural checks for the page script in frontend/index.html, run with plain Node (no dependencies):
//   node tests/ui_flow.js
// It loads the real <script> into a stub DOM with a fake fetch, so the two-stage flow is tested without a browser.
// tests/test_ui_flow.py runs it under pytest when Node is installed.
const fs = require("fs"), vm = require("vm");
const html = fs.readFileSync(require("path").join(__dirname, "..", "frontend", "index.html"), "utf8");
const js = html.match(/<script>([\s\S]*)<\/script>/)[1];

class El {
  constructor() { this._t = ""; this.children = []; this.style = { setProperty() {} }; this.hidden = false; this.className = "";
    this.value = ""; this.files = []; this.disabled = false; this.offsetWidth = 0; const s = new Set();
    this.classList = { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c) }; this._s = s;
    this.attrs = {}; this.focused = false; this.src = ""; this.alt = ""; this.placeholder = ""; }
  set textContent(v) { this._t = v; } get textContent() { return this._t; }
  set innerHTML(v) { this._h = v; if (v === "") this.children = []; } get innerHTML() { return this._h || ""; }
  appendChild(c) { this.children.push(c); } scrollIntoView() {} requestSubmit() {} focus() { this.focused = true; }
  setAttribute(k, v) { this.attrs[k] = v; }
}
// healthImpl answers GET /api/health (the page pings it on load); everything else goes to fetchImpl.
function makeEnv(fetchImpl, healthImpl = () => Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) })) {
  const els = new Proxy({}, { get: (t, id) => (t[id] ||= new El()) });
  const document = { getElementById: id => els[id], createElement: () => new El(), documentElement: { lang: "" } };
  class FormData { constructor() { this.m = {}; } append(k, v) { this.m[k] = v; } get(k) { return this.m[k]; } }
  class AbortController { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; this.onabort && this.onabort(); } }
  const revoked = [];
  const URL = { createObjectURL: f => "blob:" + f.name, revokeObjectURL: u => revoked.push(u) };
  const fetch = (url, o) => (String(url).endsWith("/api/health") ? healthImpl(url) : fetchImpl(url, o));
  const ctx = { document, FormData, AbortController, AbortSignal: { timeout: () => ({}) }, URL, fetch, setTimeout, clearTimeout,
                window: { SATARK_WAKE_MS: 40 }, console, Promise };
  vm.createContext(ctx); vm.runInContext(js, ctx);
  return { els, ctx, document, revoked, submit: () => els["form"].onsubmit({ preventDefault() {} }) };
}
const res = (d, ok = true) => Promise.resolve({ ok, json: () => Promise.resolve(d) });
const base = { verdict: "scam", headline: "H", scam_type: "t", explanation: "e", signals: [], llm_flags: [], highlights: [], actions: [], analysed_text: "msg", ai_error: null };
const stage1 = { ...base, risk: 35, scores: { rules: 35, llm: null }, ai_used: false };
const stage2 = { ...base, risk: 82, scores: { rules: 35, llm: 95 }, ai_used: true };
let failed = 0; const check = (name, cond) => { console.log((cond ? "PASS " : "FAIL ") + name); if (!cond) failed++; };
const tick = () => new Promise(r => setTimeout(r, 5));

(async () => {
  // 1. AI raises the risk
  { const seen = []; let release;
    const gate = new Promise(r => release = r);
    const e = makeEnv(async (url, o) => { seen.push(o.body.get("ai")); if (o.body.get("ai") === "false") return res(stage1); await gate; return res(stage2); });
    e.els["text"].value = "some message";
    const done = e.submit(); await tick(); await tick();
    check("stage 1 renders rules result before the AI answers", e.els["risk"].textContent === 35);
    check("pending note shown", e.els["aiNote"].textContent.includes("AI is double-checking") && e.els["aiNote"].className.includes("pending"));
    check("button usable again while AI runs", e.els["go"].disabled === false);
    release(); await done;
    check("calls were ai=false then ai=true", seen.join() === "false,true");
    check("result updated in place to the AI risk", e.els["risk"].textContent === 82);
    check("delta message", e.els["delta"].textContent === "AI raised risk: 35 → 82" && !e.els["delta"].hidden);
    check("pending note cleared", e.els["aiNote"].textContent === ""); }
  // 2. AI returns a rules-only answer (provider failed server-side)
  { const e = makeEnv(async (u, o) => res(o.body.get("ai") === "false" ? stage1 : { ...stage1, ai_error: "unavailable" }));
    e.els["text"].value = "m"; await e.submit();
    check("failed AI: rules result kept", e.els["risk"].textContent === 35);
    check("failed AI: note says so", e.els["aiNote"].textContent.includes("AI unavailable, rules-only result") && e.els["aiNote"].className.includes("warn")); }
  // 3. network error on stage 2
  { const e = makeEnv(async (u, o) => { if (o.body.get("ai") === "true") throw new Error("net"); return res(stage1); });
    e.els["text"].value = "m"; await e.submit();
    check("network failure on stage 2: rules result kept + note", e.els["risk"].textContent === 35 && e.els["aiNote"].textContent.includes("AI unavailable")); }
  // 4. AI agrees (same risk)
  { const e = makeEnv(async (u, o) => res(o.body.get("ai") === "false" ? stage1 : { ...stage2, risk: 35 }));
    e.els["text"].value = "m"; await e.submit();
    check("no raise: says AI agrees", e.els["delta"].textContent === "AI agrees with the rules"); }
  // 5. stale response is ignored
  { let releaseFirst; const gate = new Promise(r => releaseFirst = r); let n = 0;
    const e = makeEnv(async (u, o) => { const ai = o.body.get("ai"); const t = o.body.get("text");
      if (ai === "false") return res({ ...stage1, risk: t === "one" ? 10 : 20, scores: { rules: 10, llm: null } });
      if (t === "one") { await gate; return res({ ...stage2, risk: 99 }); } return res({ ...stage2, risk: 77 }); });
    e.els["text"].value = "one"; const p1 = e.submit(); await tick(); await tick();
    e.els["text"].value = "two"; const p2 = e.submit(); await p2;
    releaseFirst(); await p1;
    check("older slow response did not overwrite the newer result", e.els["risk"].textContent === 77); }
  // 6. screenshot only: no instant stage, friendly error on failure
  { const seen = [];
    const e = makeEnv(async (u, o) => { seen.push(o.body.get("ai")); return res({ detail: "Couldn't read that screenshot right now. Try again, or paste the message text." }, false); });
    e.els["image"].files = [{ name: "s.png" }]; await e.submit();
    check("image-only skips the ai=false call", seen.join() === "true");
    check("friendly error shown", e.els["error"].textContent.startsWith("Couldn't read that screenshot")); }
  // 7. stage 1 error (e.g. empty input) shows the message and never calls stage 2
  { const seen = []; const e = makeEnv(async (u, o) => { seen.push(o.body.get("ai")); return res({ detail: "Paste a message or upload a screenshot." }, false); });
    await e.submit();
    check("stage-1 error shown, stage 2 not attempted", seen.join() === "false" && e.els["error"].textContent.startsWith("Paste")); }
  // 8. Translations: headline, section titles, page text, gauge label, and re-titling on language change
  { const e = makeEnv(async (u, o) => res(o.body.get("ai") === "false" ? stage1 : stage2));
    check("english by default", e.els["hWhy"].textContent === "Why" && e.els["go"].textContent === "Check message" && e.document.documentElement.lang === "en");
    e.els["lang"].value = "hi"; e.els["lang"].onchange();
    check("hindi: html lang, titles and button", e.document.documentElement.lang === "hi" && e.els["hWhy"].textContent === "कारण"
      && e.els["hSteps"].textContent === "अब क्या करें" && e.els["go"].textContent === "संदेश जाँचें");
    check("hindi: placeholder, aria-label and footer", e.els["text"].placeholder.startsWith("मिला हुआ") && e.els["lang"].attrs["aria-label"] === "स्पष्टीकरण की भाषा"
      && e.els["footer"].innerHTML.includes("1930"));
    e.els["text"].value = "m"; await e.submit();
    check("hindi: translated headline for the verdict", e.els["headline"].textContent === "यह संदेश धोखाधड़ी लगता है");
    check("hindi: gauge is labelled for assistive tech", e.els["gauge"].attrs["aria-label"] === "जोखिम 82/100");
    check("hindi: delta message", e.els["delta"].textContent === "AI ने जोखिम बढ़ाया: 35 → 82");
    e.els["lang"].value = "mr"; e.els["lang"].onchange();
    check("switching language re-titles the result on screen", e.els["headline"].textContent === "हा संदेश फसवणूक वाटतो" && e.els["hFlags"].textContent.startsWith("आम्हाला"));
    e.els["lang"].value = "en"; e.els["lang"].onchange();
    check("the AI note follows the language too", e.els["aiNote"].textContent === ""); }
  { const e = makeEnv(async (u, o) => res(o.body.get("ai") === "false" ? stage1 : { ...stage1, ai_error: "unavailable" }));
    e.els["text"].value = "m"; await e.submit();
    e.els["lang"].value = "hi"; e.els["lang"].onchange();
    check("a settled warning note is re-translated on language change", e.els["aiNote"].textContent === " · AI उपलब्ध नहीं, केवल नियमों का परिणाम" && e.els["aiNote"].className.includes("warn"));
    e.els["lang"].value = "xx"; e.els["lang"].onchange();
    check("unknown language falls back to English", e.els["hWhy"].textContent === "Why"); }
  // 9. Screen-reader announcements
  { const e = makeEnv(async (u, o) => res(o.body.get("ai") === "false" ? stage1 : stage2));
    const said = []; Object.defineProperty(e.els["announce"], "textContent", { set(v) { if (v) said.push(v); }, get() { return ""; } });
    e.els["text"].value = "m"; await e.submit();
    check("announces the instant result with its risk", said[0] === "This looks like a scam. Risk 35 out of 100. AI is double-checking…");
    check("announces the AI update", said[1] === "AI raised risk: 35 → 82. This looks like a scam. Risk 82 out of 100"); }
  { const e = makeEnv(async (u, o) => res(o.body.get("ai") === "false" ? stage1 : { ...stage1, ai_error: "unavailable" }));
    const said = []; Object.defineProperty(e.els["announce"], "textContent", { set(v) { if (v) said.push(v); }, get() { return ""; } });
    e.els["text"].value = "m"; await e.submit();
    check("announces when the AI is unavailable", said[1] === "AI unavailable, rules-only result"); }
  // 10. Screenshot preview and remove button
  { const e = makeEnv(async () => res(stage1));
    e.els["image"].files = [{ name: "shot.png" }]; e.els["image"].onchange({ target: e.els["image"] });
    check("preview shown with thumbnail and file name", e.els["preview"].hidden === false && e.els["thumb"].src === "blob:shot.png" && e.els["fileName"].textContent === "shot.png");
    check("drop zone marked as holding a file", e.els["drop"]._s.has("has"));
    check("remove button has an accessible name", e.els["removeImg"].attrs["aria-label"] === "Remove screenshot");
    e.els["removeImg"].onclick();
    check("remove hides the preview, frees the blob and clears the input", e.els["preview"].hidden === true && e.revoked.includes("blob:shot.png")
      && e.els["image"].value === "" && !e.els["drop"]._s.has("has"));
    check("remove returns focus to the file input", e.els["image"].focused === true); }
  // 11. "Waking up the server…" for a slow (sleeping) host
  { let wakeUp; const asleep = new Promise(r => wakeUp = r);
    const e = makeEnv(async () => res(stage1), () => asleep.then(() => ({ ok: true, json: async () => ({ ok: true }) })));
    await new Promise(r => setTimeout(r, 90));
    check("banner appears when /api/health is slow", e.els["wake"].hidden === false && e.els["wake"].textContent.startsWith("Waking up the server"));
    e.els["lang"].value = "hi"; e.els["lang"].onchange();
    check("banner is translated", e.els["wake"].textContent.startsWith("सर्वर चालू हो रहा है"));
    wakeUp(); await tick(); await tick();
    check("banner hides once the server answers", e.els["wake"].hidden === true); }
  { const e = makeEnv(async () => res(stage1));
    await new Promise(r => setTimeout(r, 90));
    check("fast server: banner never shown", e.els["wake"].hidden === true); }
  { let wakeUp; const asleep = new Promise(r => wakeUp = r);
    const e = makeEnv(async (u, o) => { if (o.body.get("ai") === "false") await asleep; return res(o.body.get("ai") === "false" ? stage1 : stage2); });
    e.els["text"].value = "m"; const done = e.submit();
    await new Promise(r => setTimeout(r, 90));
    check("banner also shows while the first check is waiting on a sleeping server", e.els["wake"].hidden === false);
    wakeUp(); await done;
    check("and hides when the result arrives", e.els["wake"].hidden === true && e.els["risk"].textContent === 82); }
  console.log(failed ? `${failed} FAILED` : "all UI-flow checks passed"); process.exit(failed ? 1 : 0);
})();


