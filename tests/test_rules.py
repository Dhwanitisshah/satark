import json
from pathlib import Path

import pytest

from app.rules.engine import extract_urls, run_rules
from app.verdict import band

SAMPLES = json.loads((Path(__file__).resolve().parents[1] / "samples" / "messages.json").read_text("utf-8"))


# Samples the rules currently get wrong. Each is a strict xfail, so fixing one makes the test
# "unexpectedly pass" and forces its removal from this set. Rules-blind samples are never listed here.
KNOWN_GAPS: set[str] = {
    "anydesk-refund", "bijli-hinglish", "digital-arrest-hindi", "task-hinglish", "stock-tips-fee",
    "wa-recruiter-foreign", "electricity-marathi", "family-hindi-new-number", "digital-arrest-hinglish",
    "otp-hinglish", "inaam-hinglish", "job-deposit", "genuine-delivery-otp", "genuine-rbi-advisory",
}


def _param(s: dict):
    marks = [pytest.mark.xfail(strict=True, reason="known rule gap")] if s["id"] in KNOWN_GAPS else []
    return pytest.param(s, id=s["id"], marks=marks)


@pytest.mark.parametrize("sample", [_param(s) for s in SAMPLES])
def test_sample_bands(sample):
    result = run_rules(sample["text"])
    got = band(result["score"])
    order = {"low": 0, "suspicious": 1, "scam": 2}
    if sample["expected"] == "low":
        assert got == "low", result["signals"]
    elif sample.get("rules_blind"):
        # These exist to show what the LLM adds. If a rule starts catching one, the flag is stale.
        assert got == "low", f"no longer rules-blind: {result['signals']}"
    else:
        assert order[got] >= order[sample["expected"]], result["signals"]


def test_sample_set_composition():
    ids = [s["id"] for s in SAMPLES]
    assert len(ids) == len(set(ids)), "duplicate sample ids"
    blind = [s for s in SAMPLES if s.get("rules_blind")]
    genuine = [s for s in SAMPLES if s["expected"] == "low"]
    scams = [s for s in SAMPLES if s["expected"] != "low" and not s.get("rules_blind")]
    assert len(SAMPLES) >= 45
    assert len(scams) >= 28 and len(genuine) >= 12 and len(blind) >= 6
    assert all(s["expected"] == "scam" for s in blind)
    assert all(s["expected"] in {"scam", "suspicious", "low"} for s in SAMPLES)


def test_negated_otp_is_not_flagged():
    ids = {s["id"] for s in run_rules("Do not share OTP with anyone.")["signals"]}
    assert "otp_request" not in ids


def test_otp_request_flagged():
    ids = {s["id"] for s in run_rules("Please share the OTP you received to complete verification")["signals"]}
    assert "otp_request" in ids


def test_lookalike_vs_official():
    fake = {s["id"] for s in run_rules("Login at https://hdfc-netbanking-secure.top/verify")["signals"]}
    real = {s["id"] for s in run_rules("Login at https://netbanking.hdfcbank.com")["signals"]}
    assert "url_lookalike" in fake and "url_tld" in fake
    assert "url_lookalike" not in real


def test_bank_in_domains_trusted():
    ids = {s["id"] for s in run_rules("Visit https://sbi.bank.in for details")["signals"]}
    assert "url_lookalike" not in ids


def test_extract_bare_domains_but_not_emails():
    urls = extract_urls("mail me at a@gmail.com or visit sbi-reward.xyz now")
    assert urls == ["sbi-reward.xyz"]


def test_every_signal_has_evidence_in_text():
    for s in SAMPLES:
        for sig in run_rules(s["text"])["signals"]:
            assert sig["evidence"].lower() in s["text"].lower(), (s["id"], sig)
