# Satark: project context for Claude

ForgeHacks 2026 project, mid-build. Track: **AI + Cybersecurity** ("help people recognize, prevent,
verify, or respond to scams, impersonation, and fraud enabled by AI or modern technologies").

Satark is an India-first AI scam checker. The user pastes a suspicious SMS/WhatsApp message/call
script or uploads a screenshot and gets:
- a verdict (`scam` / `suspicious` / `low`) with a 0-100 risk score,
- the exact words that give it away (highlighted),
- deterministic next steps (1930, cybercrime.gov.in, Sanchar Saathi Chakshu),
- an explanation in English, Hindi or Marathi.

## Deadline

Submissions lock **Sat Oct 10, 12:00 PM**. Treat that as IST. Target submitting by **Fri Oct 9, 11 PM IST**.
The day-by-day plan, demo script and submission checklist are in [PLAN.md](PLAN.md).

## Hard rules

1. **No reused code.** ForgeHacks forbids reusing pre-existing projects. Never copy from the user's
   other repos (e.g. FAKE-NEWS-DETECTOR). Open-source libraries are fine.
2. **Commit small and often** with clear messages. Never squash or rewrite history (no `--amend` on
   pushed commits, no rebase, no force-push). The history proves the work happened Oct 6-10.
3. **Rules are hard evidence.** Fusion in `backend/app/verdict.py` is
   `risk = max(rules, 0.6*llm + 0.4*rules)`. Never let the LLM lower a rule hit.
4. **Evidence must be verbatim.** Every signal's `evidence` must appear in the message text; LLM
   quotes not in the text are dropped. Keep that guard.
5. **Next-step advice stays deterministic** (`backend/app/actions.py`). The LLM never writes safety
   instructions.
6. **Genuine messages in `samples/messages.json` must stay `low`.** Run `python -m pytest -q` and
   `python scripts\eval.py` after every rules change.
7. **Secrets only in `.env`** (gitignored). Never commit or print keys, and don't `cat` `.env`.

## Environment

- Windows + PowerShell. venv at `.venv` (`.venv\Scripts\Activate.ps1`).
- Server runs from `backend/`: `uvicorn app.main:app --reload`. It also serves the UI at `/`.
- Use `curl.exe`, not `curl` (PowerShell aliases `curl` to `Invoke-WebRequest`).
- `scripts\smoke_test.ps1` needs PowerShell 7 (`pwsh`), because it uses `Invoke-RestMethod -Form`.
- Checks, from the repo root: `python -m pytest -q`, `python scripts\eval.py` (add `--llm` for rules + LLM).

## Status

Done: rules pipeline, FastAPI API, two-stage UI (rules first, AI updates in place) with hi/mr strings and
accessibility, Gemini LLM layer (retry, cross-provider fallback to Groq, 12s cap, LRU cache), screenshot input via
the vision model (tested with real Gemini), 63-sample set, grouped eval, `render.yaml`, 177 passing tests (no
network, plus a Node check of the page script), README Results filled in.
Latest eval (`gemini-3.1-flash-lite`): known scripts 35/35, rules-blind 0/8 rules-only -> 8/8 with the LLM,
false alarms 0/20 rules-only, 1/20 with the LLM (friend asking for Rs 500 on UPI, left alone on purpose).

Deployed: https://satark-1tnt.onrender.com (Render free web service `satark`, id `srv-db2j76qj9qps73ehj1l0`; smoke
test 4/4 and a live Hindi screenshot check passed on 2026-10-06). The service was created by hand, not via the
Blueprint, so its dashboard settings must match `render.yaml`: Root Directory `backend`, build
`pip install -r ../requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check
`/api/health`. Gotchas: with a Root Directory set, Render only auto-deploys on changes under `backend/`, and
`frontend/` and `requirements.txt` are outside it, so a push touching only those does NOT deploy unless the
service's Build Filter lists them (`backend/**`, `frontend/**`, `requirements.txt`; `render.yaml` has it, but the
dashboard service must be set by hand). Otherwise trigger a deploy through the Render MCP. `PYTHON_VERSION` was not applied, so it runs on Python 3.14.3 (works). `.mcp.json` holds
the Render API key and is gitignored; never commit it.

Not done: translated "what to do now" steps (still a fixed English playbook), native-speaker review of the hi/mr
strings, demo video, Devpost text, a per-attempt time budget (a slow primary can eat the whole 12s cap so the
fallback never runs; seen once live).

Samples: `rules_blind: true` marks scams with no keyword the rules know. They exist to show what the LLM adds, so
**never add regexes to make them pass**; a test keeps their rule score at 0. The known-script results are in-sample
(rules were tuned on them), so quote the rules-blind row when asked how well it generalises.

## Architecture

Request flow for `POST /api/check` (multipart: `text`, `image`, `lang` = `en`|`hi`|`mr`, `ai` = true|false):

1. `backend/app/main.py`: FastAPI app. Validates input (5000 chars of text, 5 MB image), loads `.env`,
   serves `frontend/` at `/` and `/static`. Screenshots go through local Tesseract OCR if installed,
   otherwise the vision LLM reads them (`extracted_text`), otherwise a 422; an unreadable screenshot is a friendly
   503, never a false "low". `ai=false` skips the LLM and returns the rules verdict at once (the UI calls it first).
   `GET /api/health` reports `llm`, `vision`, `fallback` and `ocr` availability.
2. `backend/app/rules/engine.py`: deterministic engine. About 18 regex `Pattern`s for Indian scam scripts
   (digital arrest, KYC block, task jobs, UPI PIN-to-receive, OTP request, APK and so on; some are
   `negatable`, so "do not share OTP" isn't flagged), plus URL checks (look-alike brand domains, throwaway TLDs,
   shorteners, punycode, raw IP, `.apk`), UPI ID checks, and foreign phone numbers. Each `Signal` has an
   `evidence` string; the score is the sum of weights, capped at 100. Reference lists live in
   `rules/domains.py` (official domains, brand tokens, TLDs, UPI handles).
3. `backend/app/llm.py`: optional OpenAI-compatible chat call (Gemini by default, also used for vision; keep the
   code provider-agnostic), configured by `LLM_*` env vars. Rule signals are passed as hints. It returns JSON
   (`risk`, `scam_type`, `red_flags`, `explanation`, `extracted_text`), or `None` if unconfigured. On failure it
   retries 500/503 once after 2s (never 429), then tries the fallback once, all under a 12s total cap
   (`LLM_TOTAL_TIMEOUT`; 18s for screenshot-only, `LLM_TOTAL_TIMEOUT_VISION`), then raises `LLMError(reason)` with
   `rate_limited` | `unavailable` | `bad_response`. `main.py` catches it, the rules still answer, and the API returns
   `ai_error`. The fallback (`LLM_FALLBACK_BASE_URL` / `_API_KEY` / `_MODEL`) can be another provider (Groq,
   `qwen/qwen3.8-27b`; note `llama-3.3-70b-versatile` is NOT on this key). It is text-only, so it is skipped for
   screenshot-only requests, and the primary's key is never sent to a different host. The answer carries
   `provider`, surfaced as `ai_provider`. Groq's free tier is ~8k tokens/min, so pace evals at >=12s per call.
   Failures are logged with provider, model and status only, never the key or message text. Successful judgements
   are cached in memory (LRU, 256 entries, keyed on text hash + image hash + lang + model; failures never cached).
   Tests clear every `LLM_*` var and the cache (`tests/conftest.py`), so `.env` can't leak in and pytest never
   calls a real provider.
4. `backend/app/verdict.py`: `fuse()` combines the two (rule 3 above), drops LLM quotes that aren't in the
   text, builds highlights, and bands the score: scam >= 50, suspicious >= 20, else low.
5. `backend/app/actions.py`: fixed playbook of next steps keyed by signal id and verdict.
6. `backend/app/ocr.py`: optional pytesseract wrapper (`eng+hin`, falls back to `eng`).

Other directories:
- `frontend/index.html`: single-file vanilla HTML/CSS/JS UI, no build step. Two-stage flow (`ai=false` first, then
  `ai=true` updates the card in place, 20s abort, stale-response token), hi/mr strings in `I18N` (headline, section
  titles, page text; advice steps stay English), screenshot preview, "Waking up the server..." banner, a live region
  for screen readers. Set `window.SATARK_API` to point it at another origin. Keep it dependency-free.
- `render.yaml`: Render Blueprint (free web service, root `backend/`). `requirements.txt` is runtime only;
  `requirements-dev.txt` adds pytest and Pillow.
- `samples/screenshots/` and `scripts/make_screenshots.py`: synthetic chat screenshots for the vision path.
- `samples/messages.json`: labelled messages (`id`, `label`, `expected`, `text`). Used by the tests and the eval.
- `scripts/eval.py`: per-sample table plus grouped summary (all / known scripts / rules-blind / genuine); `--llm`
  adds rules+LLM side by side and an AI-scored count, `--delay N` paces calls (default 4s). For measurement runs
  set `LLM_TOTAL_TIMEOUT=30` so the 12s UI cap doesn't distort results.
  `scripts/smoke_test.ps1`: end-to-end check against a running server (`-Base <url>`).
- `tests/`: pytest. `test_rules.py` runs every sample and checks the evidence guard; `test_api.py` uses
  `TestClient` and monkeypatches `llm.analyse` to test fusion. `test_llm.py` mocks httpx for every failure path, the
  cache and the cross-provider fallback; `test_vision.py` covers screenshots; `test_ai_flag.py` the two-stage flag;
  `ui_flow.js` (run by `test_ui_flow.py` when Node is installed) loads the real page script into a stub DOM.
  `conftest.py` puts `backend/` on `sys.path` and clears every `LLM_*` var. A stub DOM can't see layout, so for CSS
  changes drive the page in headless Edge (kill only the process you started, never all of msedge).
- `docs/screenshots/`: README images. `.env.example`: the config template.
