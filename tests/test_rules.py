import json
from pathlib import Path

import pytest

from app.rules.engine import extract_urls, run_rules
from app.verdict import band

SAMPLES = json.loads((Path(__file__).resolve().parents[1] / "samples" / "messages.json").read_text("utf-8"))


@pytest.mark.parametrize("sample", SAMPLES, ids=[s["id"] for s in SAMPLES])
def test_sample_bands(sample):
    result = run_rules(sample["text"])
    got = band(result["score"])
    order = {"low": 0, "suspicious": 1, "scam": 2}
    if sample["expected"] == "low":
        assert got == "low", result["signals"]
    else:
        assert order[got] >= order[sample["expected"]], result["signals"]


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
