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
    this.classList = { add: c => s.add(c), remove: c => s.delete(c), contains: c => s.has(c) }; this._s = s; }
  set textContent(v) { this._t = v; } get textContent() { return this._t; }
  set innerHTML(v) { this._h = v; if (v === "") this.children = []; } get innerHTML() { return this._h || ""; }
  appendChild(c) { this.children.push(c); } scrollIntoView() {} requestSubmit() {} setAttribute() {}
}
function makeEnv(fetchImpl) {
  const els = new Proxy({}, { get: (t, id) => (t[id] ||= new El()) });
  const document = { getElementById: id => els[id], createElement: () => new El() };
  class FormData { constructor() { this.m = {}; } append(k, v) { this.m[k] = v; } get(k) { return this.m[k]; } }
  class AbortController { constructor() { this.signal = { aborted: false }; } abort() { this.signal.aborted = true; this.onabort && this.onabort(); } }
  const ctx = { document, FormData, AbortController, fetch: fetchImpl, setTimeout, clearTimeout, window: {}, console, Promise };
  vm.createContext(ctx); vm.runInContext(js, ctx);
  return { els, ctx, submit: () => els["form"].onsubmit({ preventDefault() {} }) };
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
  console.log(failed ? `${failed} FAILED` : "all UI-flow checks passed"); process.exit(failed ? 1 : 0);
})();


