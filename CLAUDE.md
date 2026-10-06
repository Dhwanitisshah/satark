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

Done: rules-only pipeline, FastAPI API, static UI, 26 passing tests, eval script (10/10 scams caught,
0/4 false alarms on the 14 samples).

Not done: real LLM run, bigger sample set (target 40+, at least 12 genuine), screenshot input in
deployment, deployment, Hindi/Marathi polish, demo video, Devpost text.

## Architecture

Request flow for `POST /api/check` (multipart: `text`, `image`, `lang` = `en`|`hi`|`mr`):

1. `backend/app/main.py`: FastAPI app. Validates input (5000 chars of text, 5 MB image), loads `.env`,
   serves `frontend/` at `/` and `/static`. Screenshots go through local Tesseract OCR if installed,
   otherwise the vision LLM reads them (`extracted_text`), otherwise a 422. `GET /api/health` reports
   `llm`, `vision` and `ocr` availability.
2. `backend/app/rules/engine.py`: deterministic engine. About 18 regex `Pattern`s for Indian scam scripts
   (digital arrest, KYC block, task jobs, UPI PIN-to-receive, OTP request, APK and so on; some are
   `negatable`, so "do not share OTP" isn't flagged), plus URL checks (look-alike brand domains, throwaway TLDs,
   shorteners, punycode, raw IP, `.apk`), UPI ID checks, and foreign phone numbers. Each `Signal` has an
   `evidence` string; the score is the sum of weights, capped at 100. Reference lists live in
   `rules/domains.py` (official domains, brand tokens, TLDs, UPI handles).
3. `backend/app/llm.py`: optional OpenAI-compatible chat call (Gemini by default, also used for vision; keep the
   code provider-agnostic), configured by `LLM_*` env vars. Rule signals are passed as hints. It returns JSON
   (`risk`, `scam_type`, `red_flags`, `explanation`, `extracted_text`), or `None` if unconfigured. On failure it
   retries 429/500/503 (2s, 5s), tries `LLM_FALLBACK_MODEL` once, then raises `LLMError(reason)` with `rate_limited`
   | `unavailable` | `bad_response`. `main.py` catches it, the rules still answer, and the API returns `ai_error`.
   Failures are logged with provider, model and status only, never the key or message text.
4. `backend/app/verdict.py`: `fuse()` combines the two (rule 3 above), drops LLM quotes that aren't in the
   text, builds highlights, and bands the score: scam >= 50, suspicious >= 20, else low.
5. `backend/app/actions.py`: fixed playbook of next steps keyed by signal id and verdict.
6. `backend/app/ocr.py`: optional pytesseract wrapper (`eng+hin`, falls back to `eng`).

Other directories:
- `frontend/index.html`: single-file vanilla HTML/CSS/JS UI (sample chips, language select, highlighted
  message, flags, steps). Set `window.SATARK_API` to point it at another origin.
- `samples/messages.json`: labelled messages (`id`, `label`, `expected`, `text`). Used by the tests and the eval.
- `scripts/eval.py`: catch-rate / false-alarm table (`--llm` to include the LLM).
  `scripts/smoke_test.ps1`: end-to-end check against a running server (`-Base <url>`).
- `tests/`: pytest. `test_rules.py` runs every sample and checks the evidence guard; `test_api.py` uses
  `TestClient` and monkeypatches `llm.analyse` to test fusion. `conftest.py` puts `backend/` on `sys.path`.
- `docs/screenshots/`: README images. `.env.example`: the config template.
