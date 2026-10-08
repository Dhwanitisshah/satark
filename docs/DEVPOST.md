# Devpost submission draft: Satark

Paste each section into the matching field of the Devpost form. Links and numbers are current as of 2026-10-08.
Before submitting, fill the two `TODO` lines (video link, and anything personal you want in "Inspiration").

## Title

**Satark**

## Tagline (one line)

An India-first AI scam checker: paste a suspicious message or screenshot and get a verdict, the exact words that give it away, and what to do next, in English, Hindi or Marathi.

## Track

**AI + Cybersecurity**

## Links

- Live demo: https://satark-1tnt.onrender.com (free tier: the first load can take about a minute)
- Code: https://github.com/Dhwanitisshah/satark
- Demo video: TODO (paste the YouTube link)
- Slides/images: `docs/results.png` (results), `docs/architecture.png` (architecture), `docs/screenshots/` (the app)

## Inspiration

Cyber fraud in India is growing fast. The Ministry of Home Affairs told Parliament that losses reported on the National Cybercrime Reporting Portal went from ₹2,290 crore in 2022 to ₹7,465 crore in 2023 and ₹22,846 crore in 2024 (Lok Sabha Unstarred Question 432, 2 December 2025). Behind those numbers are scripts that keep changing: "digital arrest" video calls, fake KYC and electricity-bill SMSes, "like videos and earn" task jobs, UPI "scan to receive cashback" tricks, and now cloned voices of relatives asking for money.

The people hit hardest, such as parents, first-time smartphone users and students looking for work, often have nobody to ask "is this real?" in the minute it matters. The advice exists, but it is spread across advisories they never see. I wanted a second opinion that is one paste away, speaks their language, and tells them what to do, not just a label.

<!-- TODO (optional): add a sentence of your own about why this problem matters to you. -->

## What it does

You paste an SMS, WhatsApp message or call script, or add a screenshot of it. Satark answers with:

1. **A verdict and a 0–100 risk score**: scam, suspicious, or low risk.
2. **Why**: the exact words that give it away, highlighted in the message, with a plain-language reason for each red flag.
3. **What to do now**: concrete next steps, including calling 1930, filing at cybercrime.gov.in, and reporting the number on Sanchar Saathi Chakshu.
4. **An explanation in English, Hindi or Marathi.**

The rules answer instantly, and the AI check updates the same card a moment later, with a small badge when it changes the result ("AI raised risk: 0 → 57"). It is also an installable web app: on Android you can use Share → Satark straight from WhatsApp or SMS.

## How I built it

Satark has two layers, on purpose.

- **A rule engine** (Python, no AI): about 20 patterns for Indian scam scripts (digital arrest, KYC block, task jobs, UPI PIN-to-receive, OTP requests, remote-access apps and more), plus checks on links (look-alike bank domains like `sbi-kyc-update.xyz`, throwaway TLDs, shorteners, punycode, `.apk` files), UPI IDs and foreign numbers posing as local. It is instant, free, explainable, and every flag carries the exact words it matched.
- **An LLM layer** for what regexes can't see: reworded scripts, tone, context. Groq (`qwen3.8-27b`) answers text checks first because it is fast; Gemini (`gemini-3.5-flash-lite`) is the fallback and the only model that reads screenshots. Both are called through OpenAI-compatible endpoints, with one retry, a 12-second cap split into stages, and an in-memory cache.

Three rules keep the AI honest:

1. **The AI can raise a score, never lower a rule hit:** `risk = max(rules, 0.6 × AI + 0.4 × rules)`.
2. **Evidence must be verbatim:** any quote the AI gives that is not literally in the message is dropped, so a hallucinated "red flag" never reaches the user.
3. **Safety advice is fixed text:** next steps come from a reviewed playbook in code, never from the AI.

The front end is one static page (vanilla HTML/CSS/JS, no build step) with the two-stage result, Hindi and Marathi, keyboard and screen-reader support, a PWA manifest with a Web Share Target, and a service worker that caches only the page shell and never a verdict. The backend is FastAPI on Render's free tier. I wrote 250+ automated tests, including a Node harness that runs the real page script against a stub DOM, and a labelled set of 63 messages with an eval script.

## Challenges I ran into

- **Making it fast on a free server.** Early screenshot checks took about 16 seconds (18.6 worst), and one in three failed. Profiling showed each check built its own HTTP client, costing about 225 ms of CPU on a throttled free instance plus fresh TLS handshakes. I moved to one shared keep-alive client with a warm-up request at startup, added per-call timing so I could see connect, TLS and first-byte times, and switched screenshot reading to Gemini 3.5 flash-lite (the older model needed 15–18 seconds for the same image). Measured against the live site: text checks take 1.05 s median (2.0 s worst) and screenshots 7.6 s median (10.0 s worst), and all 13 checks got an AI answer.
- **Choosing and swapping models.** I started with Gemini as the primary. It went over its 7-second budget on 37 of the 63 samples in one run. Groq was much faster but text-only and limited to about 8,000 tokens a minute, and the model I first wanted (`llama-3.3-70b-versatile`) turned out not to exist on my key. I ended up with Groq's `qwen3.8-27b` first and Gemini behind it, with staged time budgets so a slow primary can't use up the whole 12 seconds, and I tested the fallback both ways by deliberately breaking each provider: 43/43 scams caught either way.
- **Keeping the LLM from overruling hard evidence.** The risky failure is an AI that talks itself out of a real warning sign. So the fusion can only go up from the rule score, quotes are verified against the text, and the next steps never come from the model. I also added "rules-blind" scams (messages with no keyword the rules know) and a test that keeps their rule score at 0, so I can't quietly add a regex to make the demo look better.
- **Honest evaluation, with a guard.** The rules were tuned on my first 35 known-script samples, so that row is in-sample. I report the rules-blind row as the fair measure. When I tried to improve the product, I set a rule first: keep the change only if the eval stays at 43/43 scams, 8/8 rules-blind and 0/20 false alarms with Groq answering. I tried asking the AI to write its per-quote reasons in Hindi and Marathi, and the eval caught it: a genuine electricity-bill SMS suddenly scored as a scam. I reverted it and left those reasons in English rather than trade accuracy for polish. I also left the one message Gemini flags (a friend asking for ₹500) alone instead of tuning the prompt to it.
- **A bug no unit test could see.** While photographing the live site I found that choosing a screenshot showed its preview but sent nothing: the change handler reset the file input and wiped the file it had just been given. My stub-DOM test couldn't catch it because a fake input doesn't behave like a real one. I fixed it, made the test double behave like a real file input, and verified it in a real browser.
- **Free-tier reality.** The server sleeps when idle, so the page pings it on load and shows "Waking up the server…" instead of looking frozen.

## Accomplishments that I'm proud of

- On my 63 labelled messages, in the exact deployed setup (Groq first, Gemini as fallback): **43/43 scams caught with the AI**, including **8/8 rules-blind scams that the rules alone score at 0**, and **0/20 false alarms**. All 63 got an AI score (Groq answered 61, Gemini 2). The rules alone catch 35 of 43. If Gemini answers instead of Groq, one genuine message (a friend asking for ₹500) is flagged, and I report that too.
- It checks real screenshots end to end, including a Hindi WhatsApp chat, in about 8 seconds.
- It is live, installable, and tested without a network: no test ever calls a real AI provider.
- The result explains itself: highlighted words, one reason per red flag, and next steps that are the same every time.

## What I learned

- Combine instead of choose: rules are fast and explainable, the model generalises, and the useful part is the contract between them (the AI may raise, never lower; its quotes must be real).
- Measure before optimising. The big latency win came from timing each step, not from guessing at prompts.
- Small, honest evaluation sets are worth building early. Separating the in-sample row from the rules-blind row changed how I read my own results.
- Test doubles must behave like the real thing, or they hide the bugs you most need to find.

## Real-world impact and who it's for

Satark is for anyone in India who gets a message and wonders if it is real, especially older and first-time digital-payment users and the family members who get these messages forwarded to them. It works at the moment of need (before the click or payment), teaches the pattern ("a UPI PIN only ever sends money"), and routes people to real help (1930, cybercrime.gov.in, Sanchar Saathi Chakshu) when money has already gone. The rule layer works with no AI at all, so a version can run cheaply or on a device. It is a second opinion, not a guarantee, and the interface says so.

## What's next

- Native-speaker review of the Hindi and Marathi playbook, and translating the AI's per-quote reasons (my first attempt hurt accuracy, so it needs a safer prompt and its own eval).
- A voice-note check for cloned-voice "relative in trouble" calls.
- Community-reported numbers and domains, cross-checked against Chakshu.
- An on-device, rules-only mode as a lightweight Android app, and a share path for iPhone.

## Built with

`python` · `fastapi` · `httpx` · `groq` · `qwen` · `google-gemini` · `openai-compatible-api` · `javascript` · `html` · `css` · `pwa` · `web-share-target` · `service-worker` · `render` · `pytest` · `node.js` · `pillow` · `tesseract` (optional OCR)
