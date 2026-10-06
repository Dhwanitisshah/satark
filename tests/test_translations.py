"""Hindi and Marathi: every string exists, keeps the names people must recognise, and is selected by `lang`."""
import importlib.util
import re
import string
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import actions, verdict
from app.main import app
from app.rules import translations as sig_tr
from app.rules.engine import PATTERNS, analyse_phones, analyse_url, run_rules

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# Names that must survive translation exactly as written (the user's rule: don't translate these).
KEEP = ["1930", "cybercrime.gov.in", "sancharsaathi.gov.in", "sebi.gov.in", "Sanchar Saathi", "Chakshu", "UPI",
        "OTP", "PIN", "CVV", "KYC", "AnyDesk", "TeamViewer", "SEBI", "CBI", "APK", "QR", "WhatsApp", "Telegram",
        "Play Store", ".gov.in", ".apk"]


def placeholders(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def term_present(term: str, text: str) -> bool:
    # Short upper-case names must match as whole tokens: "PIN" must not be satisfied by "UPI PIN" alone elsewhere
    # in the string, and "OTP" must not hide inside a longer word.
    if term.isalpha() and term.isupper():
        return re.search(rf"(?<![A-Za-z]){term}(?![A-Za-z])", text) is not None
    return term in text


def pairs():
    """(where, english, hindi, marathi) for every translated string in the app."""
    for en, (hi, mr) in actions.TRANSLATIONS.items():
        yield "action", en, hi, mr
    for en, (hi, mr) in verdict.TRANSLATIONS.items():
        yield "verdict", en, hi, mr
    english = {p.id: (p.label, p.why) for p in PATTERNS} | sig_tr.ENGLISH_NON_PATTERN
    for sid, (label, why) in english.items():
        t = sig_tr.SIGNALS[sid]
        yield f"signal {sid} label", label, *t["label"]
        yield f"signal {sid} why", why, *t["why"]


# --- completeness ---------------------------------------------------------------------------------

def test_every_english_step_has_a_translation_and_there_are_no_orphans():
    assert set(actions.TRANSLATIONS) == actions.all_english_steps()


def test_every_signal_the_engine_can_raise_has_translated_text():
    ids = {p.id for p in PATTERNS} | set(sig_tr.ENGLISH_NON_PATTERN)
    assert set(sig_tr.SIGNALS) == ids
    for sid in ids:
        assert set(sig_tr.SIGNALS[sid]) == {"label", "why"}


def test_the_english_templates_still_match_what_the_engine_produces():
    """If someone edits an English label in engine.py, the translation key list would silently go stale."""
    built = {}
    for url in ["http://185.234.12.9/x", "http://xn--sbi-abc.com", "https://bit.ly/abc", "http://sbi-kyc-update.xyz/login",
                "http://x.example/app.apk"]:
        built.update({s.id: s for s in analyse_url(url)})
    built.update({s.id: s for s in analyse_phones("call +92 300 1234567")})
    for text in ["Pay the fine of Rs 500 to echallan.police@ybl", "Please send Rs 500 to rahul.k99@ybl"]:
        for sig in run_rules(text)["signals"]:       # one UPI id per message: impersonation, then a plain target
            built[sig["id"]] = type("S", (), {"label": sig["label"], "why": sig["why"], "params": sig.get("params", {})})
    for sid, (label, why) in sig_tr.ENGLISH_NON_PATTERN.items():
        assert sid in built, f"engine no longer produces {sid}"
        params = built[sid].params
        assert label.format(**params) == built[sid].label and why.format(**params) == built[sid].why, sid


@pytest.mark.parametrize("where,en,hi,mr", list(pairs()), ids=[w for w, *_ in pairs()])
def test_each_string_is_really_translated(where, en, hi, mr):
    for text in (hi, mr):
        assert DEVANAGARI.search(text), f"{where}: not Devanagari"
        assert text.strip() == text and text != en
    assert hi != mr


# --- the names people must recognise stay as they are ----------------------------------------------

@pytest.mark.parametrize("where,en,hi,mr", list(pairs()), ids=[w for w, *_ in pairs()])
def test_protected_names_and_numbers_survive_translation(where, en, hi, mr):
    for term in KEEP:
        if term_present(term, en):
            assert term_present(term, hi), f"{where}: '{term}' missing in Hindi"
            assert term_present(term, mr), f"{where}: '{term}' missing in Marathi"


@pytest.mark.parametrize("where,en,hi,mr", list(pairs()), ids=[w for w, *_ in pairs()])
def test_placeholders_match_the_english(where, en, hi, mr):
    assert placeholders(hi) == placeholders(en) == placeholders(mr)


def test_the_reporting_steps_always_carry_the_helpline_and_portals():
    for lang in ("hi", "mr"):
        steps = actions.build_actions("scam", ["otp_request"], lang)
        joined = " ".join(steps)
        assert "1930" in joined and "cybercrime.gov.in" in joined and "Chakshu" in joined


# --- selection by lang, and nothing else changes ---------------------------------------------------

SCAM = "Your SBI YONO account will be blocked today. Update KYC immediately: http://sbi-kyc-update.xyz/login"


def test_english_output_is_exactly_what_it_was():
    steps = actions.build_actions("scam", ["url_lookalike", "kyc_block"])
    assert steps[0] == "Don't click links, pay, or reply to this message." and len(steps) == 6
    assert actions.build_actions("low", []) == actions.LOW_RISK_STEPS
    assert actions.build_actions("suspicious", ["otp_request"])[-2:] == [
        actions.VERIFY_STEP, actions.REPORT_STEPS[0]]


@pytest.mark.parametrize("lang,idx", [("hi", 0), ("mr", 1)])
def test_build_actions_picks_the_requested_language(lang, idx):
    steps = actions.build_actions("scam", ["kyc_block"], lang)
    assert steps[0] == actions.TRANSLATIONS[actions.GENERIC_STEP][idx]
    assert steps[1] == actions.TRANSLATIONS[actions.SPECIFIC["kyc_block"][0]][idx]
    assert len(steps) == len(actions.build_actions("scam", ["kyc_block"]))


def test_unknown_language_falls_back_to_english():
    assert actions.build_actions("scam", ["kyc_block"], "fr") == actions.build_actions("scam", ["kyc_block"])


@pytest.mark.parametrize("lang", ["hi", "mr"])
def test_api_localizes_headline_red_flags_and_steps_but_not_the_evidence(lang):
    en = client.post("/api/check", data={"text": SCAM, "lang": "en", "ai": "false"}).json()
    body = client.post("/api/check", data={"text": SCAM, "lang": lang, "ai": "false"}).json()
    assert body["headline"] != en["headline"] and DEVANAGARI.search(body["headline"])
    assert all(DEVANAGARI.search(a) for a in body["actions"])
    for s_en, s in zip(en["signals"], body["signals"]):
        assert DEVANAGARI.search(s["label"]) and DEVANAGARI.search(s["why"])
        assert s["evidence"] == s_en["evidence"] and s["id"] == s_en["id"]      # the message's own words
        assert "params" not in s
    assert body["highlights"] == en["highlights"] and body["verdict"] == en["verdict"] and body["risk"] == en["risk"]
    assert DEVANAGARI.search(body["explanation"])      # rules-only explanation is built from the translated reasons


def test_dynamic_parts_are_filled_into_the_translation():
    body = client.post("/api/check", data={"text": SCAM, "lang": "hi", "ai": "false"}).json()
    by_id = {s["id"]: s for s in body["signals"]}
    assert "'SBI'" in by_id["url_lookalike"]["label"] and "sbi-kyc-update.xyz" in by_id["url_lookalike"]["why"]
    assert ".xyz" in by_id["url_tld"]["label"] and "{" not in by_id["url_tld"]["why"]


def test_english_api_response_is_unchanged_and_has_no_params():
    body = client.post("/api/check", data={"text": SCAM, "ai": "false"}).json()
    assert body["headline"] == "This looks like a scam"
    assert body["signals"][0]["label"].startswith("Fake 'SBI'") and all("params" not in s for s in body["signals"])


def test_the_page_and_the_server_agree_on_the_headlines():
    html = (ROOT / "frontend" / "index.html").read_text("utf-8")
    for en in (verdict.HEADLINES.values()):
        hi, mr = verdict.TRANSLATIONS[en]
        assert f'"{hi}"' in html and f'"{mr}"' in html, f"index.html and verdict.py disagree about '{en}'"


# --- the review sheet is always current ------------------------------------------------------------

def test_the_review_sheet_is_up_to_date():
    spec = importlib.util.spec_from_file_location("make_translation_review", ROOT / "scripts" / "make_translation_review.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    committed = (ROOT / "docs" / "translations_review.md").read_text("utf-8").replace("\r\n", "\n")
    assert committed == module.render_review(), "run: python scripts/make_translation_review.py"
