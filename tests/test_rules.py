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


# --- rule fixes found by the grown sample set -----------------------------------------------------

def ids_of(text: str) -> set[str]:
    return {s["id"] for s in run_rules(text)["signals"]}


def test_negation_spans_a_list_of_warnings():
    assert "remote_access" not in ids_of("Do not click unknown links or install apps like AnyDesk on a caller's word.")


def test_negation_does_not_cross_punctuation():
    # "Don't worry," ends the negation, so this is still a request to install AnyDesk.
    assert "remote_access" in ids_of("Don't worry, just install AnyDesk and read out the code.")


def test_install_anydesk_request_reaches_the_scam_band():
    assert run_rules("Please install AnyDesk so our agent can fix your refund.")["score"] >= 50


def test_delivery_otp_with_code_and_agent_is_not_an_otp_request():
    assert "otp_request" not in ids_of("Share OTP 4829 with the delivery agent only when you receive the package.")


@pytest.mark.parametrize("text", [
    "Please share the OTP with the delivery agent.",             # no code in the message itself
    "Share OTP 482910 with our bank executive to stop the debit.",  # not a delivery agent
])
def test_otp_request_still_flagged_outside_the_delivery_case(text):
    assert "otp_request" in ids_of(text)


def test_hinglish_otp_request_and_its_warning_twin():
    assert "otp_request" in ids_of("Abhi jo OTP aaya hai wo bata dijiye.")
    assert "otp_request" not in ids_of("Apna OTP kisi ko mat batana.")


def test_hinglish_otp_warning_is_not_a_secrecy_demand():
    assert "secrecy" not in ids_of("Apna OTP kisi ko mat batana, chahe wo bank wala ho.")
    assert "secrecy" in ids_of("Ye baat kisi ko mat batana.")
    assert "secrecy" in ids_of("मम्मी को मत बताना।")


def test_hinglish_hindi_marathi_scripts_are_recognised():
    assert "electricity" in ids_of("Aapka bijli connection kat diya jayega")
    assert "electricity" in ids_of("तुमचे वीज कनेक्शन कापले जाईल")
    assert "kyc_block" in ids_of("Sir aapka SBI card band ho jayega")
    assert "family_emergency" in ids_of("पापा, ये मेरा नया नंबर है")
    assert "courier_parcel" in ids_of("आपके नाम से पार्सल में ड्रग्स मिले हैं")
    assert "job_task" in ids_of("Ghar baithe kaam karein")
    assert "earnings" in ids_of("roz ₹2000 kamayein")


def test_electricity_hindi_outage_notice_is_not_a_threat():
    assert "electricity" not in ids_of("बिजली कटौती सूचना: कल सुबह 10 बजे मरम्मत कार्य होगा।")


def test_advance_fee_flagged_but_not_when_the_scammer_is_the_one_paying():
    assert "advance_fee" in ids_of("Paisa lene ke liye pehle ₹5000 fees bhejein")
    assert "advance_fee" not in ids_of("I will pay an advance of Rs 3,000 for the sofa.")


def test_job_fee():
    assert "job_fee" in ids_of("You are shortlisted. Pay a deposit of Rs 3,000 to confirm your offer letter.")
    assert "job_fee" not in ids_of("Your interview is scheduled for 10 Oct on Microsoft Teams.")


@pytest.mark.parametrize("number,flagged", [
    ("+84 912 345 678", True), ("+92 300 1234567", True), ("+234 802 123 4567", True),
    ("+91 98765 43210", False), ("+1 415 555 0132", False), ("+44 20 7946 0958", False),
])
def test_foreign_number_formats(number, flagged):
    assert ("foreign_number" in ids_of(f"WhatsApp me on {number}")) is flagged


def test_blind_samples_are_still_blind_after_the_rule_fixes():
    # The rules-blind set is there to show what the LLM adds. Rule fixes must not quietly absorb it.
    blind = [s for s in SAMPLES if s.get("rules_blind")]
    assert blind and all(run_rules(s["text"])["score"] == 0 for s in blind)


def test_new_signals_have_deterministic_advice():
    from app.actions import build_actions

    steps = build_actions("scam", ["job_fee", "advance_fee"])
    assert any("offer letter" in s for s in steps) and any("up front" in s for s in steps)
