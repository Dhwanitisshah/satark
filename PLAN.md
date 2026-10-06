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

## Submission checklist (from the ForgeHacks rules)
- [ ] Title + short description (problem + solution)
- [ ] Track: **AI + Cybersecurity**
- [ ] Public demo video, 2–4 min, on YouTube
- [ ] Public GitHub repo with a clear README
- [ ] Written description: problem and target users, technical approach and components, real-world impact
- [ ] Screenshots, architecture diagram, and deployment link
