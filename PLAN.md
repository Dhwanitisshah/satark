# Satark: ForgeHacks build plan (Oct 6 → Oct 10)

Submissions lock **Sat Oct 10, 12:00 PM**. The timezone isn't stated on the site.
**Treat the deadline as 12:00 PM IST and aim to submit by Fri Oct 9, 11 PM IST.** Everything after that is buffer.

The scaffold already works end to end with rules only: API, UI, tests, eval, screenshots.
Each day below has a goal and a "done when" line. Commit between phases.

---

## Tue Oct 6 (tonight, ~2 h): Get it running and claim resources
- [ ] Ask in the ForgeHacks Discord what timezone the deadline is in. Screenshot the answer.
- [ ] **No reuse rule:** don't copy anything from FAKE-NEWS-DETECTOR or any other earlier repo. Open-source libraries (FastAPI, httpx and so on) are fine.
- [ ] Push this repo **as-is** so its first commit is dated Oct 6. Then commit small and often through Oct 10. The commit history is your proof that everything was built during the event.
- [ ] Get a free Gemini API key (aistudio.google.com) and put it in `.env`. It also reads screenshots (`LLM_VISION=true`).
- [ ] Create the GitHub repo `satark`, push this scaffold as the first commit.
- [ ] Run locally: `pip install -r requirements.txt`, then `uvicorn app.main:app --reload` from `backend/`. Run `pytest` and `scripts/eval.py`.

**Done when:** the repo is on GitHub, tests pass, and the UI works locally.

## Wed Oct 7: Make the AI layer good
- [ ] Add your key to `.env`, run `scripts/eval.py --llm`, and pick the model with the best catch/false-alarm balance (try 2–3 Gemini models, and set `LLM_FALLBACK_MODEL` to one from a different capacity pool).
- [ ] Grow `samples/messages.json` to **40+** messages: real scam texts from I4C, RBI and PIB Fact Check advisories and news reports, plus **at least 12 genuine** messages (bank OTPs, delivery, UPI receipts, college notices). Hard negatives matter most.
- [ ] Tune `rules/engine.py` weights for any misses. Add Hinglish phrases you see in real samples.
- [ ] Check that Hindi and Marathi explanations read naturally; tweak `SYSTEM_PROMPT` if needed.
- [ ] Find one sourced stat for the README problem section (e.g. an I4C or NCRB annual cyber-fraud figure) and link it.

**Done when:** the README "Results" table has real numbers for rules-only vs rules + LLM.

## Thu Oct 8: Screenshots, polish, deploy
- [ ] Screenshot input: set the Gemini env (`LLM_VISION=true`) or install Tesseract locally. Test with 3 real-looking WhatsApp/SMS screenshots.
- [ ] UI polish: loading state, a translated headline for hi/mr, and an empty-state hint.
- [ ] Deploy. Render works well: a web service with root `backend`, build `pip install -r ../requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, and env vars from `.env`. Use the vision-LLM path there, since there's no Tesseract.
- [ ] Run `pwsh scripts/smoke_test.ps1 -Base https://<your-url>`.

**Done when:** a public URL works on your phone.

## Fri Oct 9: Stretch, assets, video, submit
- [ ] Stretch, pick ONE, only if Thursday is done:
  - a "Forward to Satark" flow (PWA share target) so users can share a message straight from WhatsApp, or
  - a voice-note check (speech-to-text, then the same pipeline) for "relative in trouble" calls.
- [ ] Export the architecture diagram (the README mermaid renders on GitHub; also screenshot it for Devpost).
- [ ] Take fresh screenshots: one scam, one genuine OTP (shows low false alarms), and one Hindi result.
- [ ] Record the demo video (script below) and upload to YouTube as Unlisted or Public.
- [ ] Write the Devpost description. Reuse the README sections: problem and users, technical approach, impact.
- [ ] **Submit by 11 PM IST.** Then re-open the Devpost page and check that the video plays and the repo is public.

## Sat Oct 10 (morning): Buffer only
Fix anything broken. No new features.

---

## Demo video script (≈3 min, max 4)

| Time | Show | Say |
|---|---|---|
| 0:00–0:25 | A phone screen with a "digital arrest" WhatsApp message | The hook: this exact script cost people their savings; most victims had no one to ask "is this real?" |
| 0:25–0:45 | The Satark home page | What it does in one line: paste or screenshot, get a verdict, why, and what to do. |
| 0:45–1:30 | Run the digital-arrest sample, then the KYC SMS | Walk through the verdict, highlighted words, red flags and next steps (1930 and cybercrime.gov.in). |
| 1:30–1:50 | Switch the language to हिंदी and run the family-emergency sample | Built for the people actually targeted, not just English speakers. |
| 1:50–2:10 | Upload a screenshot | Real users forward screenshots, not text. |
| 2:10–2:30 | Run the **genuine bank OTP** sample and show "low risk" | It doesn't cry wolf: show the eval numbers. |
| 2:30–3:00 | The architecture diagram | Rules + LLM, why the LLM can't overrule hard evidence, and why the advice is deterministic. |
| 3:00–3:15 | The roadmap slide | WhatsApp share, voice clones, on-device mode. Close on the deployed link. |

## Before recording

Do this in the ten minutes before each take. The cache lives in the server's memory, so it is lost when the free
server sleeps (about 15 minutes idle) or redeploys. Don't push to `main` between warming and recording.

- [ ] **Warm the server.** Open https://satark-1tnt.onrender.com and wait until the "Waking up the server…" banner is gone,
  or run `curl.exe -s https://satark-1tnt.onrender.com/api/health` (expect `"llm":true,"vision":true,"fallback":true`).
- [ ] **Run every demo sample once, exactly as you will on camera.** The cache key is the exact text, the language, the
  screenshot's bytes and the model, so a different language or a retyped message is a miss. The second run is served
  from the cache: the AI step takes about a second instead of one to three (a screenshot: about 1s instead of 2.6s here, and
  5–10s from Render when uncached). The rules result is instant either way.
- [ ] **Leave the rules-blind scene (#5) un-warmed**, or warm a version with one extra word. A cached answer arrives so fast
  that the "No common scam signs found" frame and the "AI raised risk: 0 → 57" badge barely show; a fresh call takes
  about 1.5s, which is the moment you want on video.
- [ ] **Pace yourself.** Groq's free tier allows about 6 checks a minute. A burst sends checks to Gemini, which is slower and
  scores a little differently. Leave a few seconds between samples.
- [ ] **Clean window.** Fresh browser window, light theme, 100% zoom, English selected (except scene 3), text box empty,
  devtools closed, notifications off.
- [ ] **Slides ready:** `docs/results.png` and `docs/architecture.png` open full-screen.
- [ ] **Android share clip (optional).** It has not been tried on a real phone. Rehearse it first and cut it if it fails.
- [ ] **After uploading:** play the YouTube link logged out, then paste it into the README (the "Demo video" line) and
  `docs/DEVPOST.md`.

### Demo samples in video order

Measured on the live site on 2026-10-08 (Groq answered unless noted). AI scores move a few points between runs;
the verdicts should not.

| # | Video time | What to do | Expected verdict | Risk (rules / AI) | What to point at |
|---|---|---|---|---|---|
| 1 | 0:45–1:10 | Click the **Digital arrest** example | **scam** | 100 (100 / 100) | highlighted "digital arrest", "do not tell anyone"; steps start with 1930 |
| 2 | 1:10–1:30 | Click **KYC SMS** | **scam** | 93 (90 / 95) | the look-alike link `sbi-kyc-update.xyz`; the badge "AI agrees" or a small rise |
| 3 | 1:30–1:50 | Pick **हिंदी**, then click **Family emergency** (warm it in Hindi too) | **scam** | 93 (90 / 95) | Hindi headline "यह संदेश धोखाधड़ी लगता है", Hindi steps; 1930 and cybercrime.gov.in stay as written |
| 4 | 1:50–2:10 | Add `samples/screenshots/family-hindi-whatsapp.png`, press Check | **scam** | 83 (65 / 95), read by Gemini | the Hindi text it read out of the picture, highlighted |
| 5 | +0:20 (new) | Paste the "Mama it's me…" message (`blind-voice-clone-mama`, from the README tour) | **scam** | 57 (0 / 95) | first "No common scam signs found" (rules score 0), then "AI raised risk: 0 → 57" |
| 6 | 2:10–2:30 | Click **Real bank OTP** | **low** | 3 (0 / 5) | it does not cry wolf; the "do not share OTP" warning isn't flagged |
| 7 | 2:30–3:15 | Show `docs/results.png`, then `docs/architecture.png` | n/a | n/a | rules-blind 0/8 → 8/8; the AI can raise a score, never lower a rule hit |

Scene 5 adds about 20 seconds to the script above. Use this sample because it has the best margin of the eight rules-blind
scams: its AI score is 95 and it finishes at **57** in both eval runs on 2026-10-08 and in the live check, seven
points over the 50 "scam" line. Some of the others finish at 51, so avoid them on camera. Say "flagged as a scam", not "100%".

## Submission checklist (from the ForgeHacks rules)
- [ ] Title + short description (problem + solution)
- [ ] Track: **AI + Cybersecurity**
- [ ] Public demo video, 2–4 min, on YouTube
- [ ] Public GitHub repo with a clear README
- [ ] Written description: problem and target users, technical approach and components, real-world impact
- [ ] Screenshots, architecture diagram, and deployment link
