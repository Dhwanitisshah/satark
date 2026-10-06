"""'What do I do now?' steps. Kept deterministic so advice is never hallucinated."""

REPORT_STEPS = [
    "If you already paid or shared an OTP: call 1930 (National Cyber Crime Helpline) right now. "
    "The first hour matters most for freezing the money.",
    "File a complaint at cybercrime.gov.in, with screenshots of the message and any payment reference.",
    "Report the sender's number on Sanchar Saathi → Chakshu (sancharsaathi.gov.in), so it can be blocked.",
]

SPECIFIC: dict[str, list[str]] = {
    "digital_arrest": [
        "Hang up. Don't stay on the video call, even if they show uniforms, badges or 'warrants'.",
        "Call your local police station on a number you look up yourself, and tell a family member.",
    ],
    "courier_parcel": [
        "Don't press any IVR option or call back. Check a real shipment only on the courier's official site.",
    ],
    "kyc_block": [
        "Don't open the link. Open your bank's official app or call the number printed on your card.",
    ],
    "electricity": [
        "Don't call the number. Check your bill status on your electricity board's official app or website.",
    ],
    "lottery_prize": ["Ignore it. You can't win something you never entered, and real prizes never ask for a fee."],
    "job_task": [
        "Don't do the 'tasks' or pay any deposit. Small early payouts are bait for a bigger deposit later.",
    ],
    "earnings": ["Ignore unsolicited job offers that promise fixed daily earnings."],
    "investment": [
        "Check the adviser's SEBI registration number on sebi.gov.in before sending anything.",
        "Leave the group. Screenshots of 'profits' in the group are fake.",
    ],
    "otp_request": ["Never share an OTP, UPI PIN, CVV or password, with anyone, for any reason."],
    "remote_access": ["Don't install AnyDesk, TeamViewer or similar. If you already did, uninstall it and call your bank."],
    "upi_receive": ["Decline the request. Entering your UPI PIN always SENDS money; it never receives it."],
    "upi_impersonation": ["Don't pay. Real fines and fees are paid on official .gov.in portals, never to personal UPI IDs."],
    "family_emergency": [
        "Call the person on their OLD number, or someone who is with them, before sending anything.",
        "Agree on a family code word for emergencies; voice clones can't guess it.",
    ],
    "apk": [
        "Don't install the file. If you did: turn on airplane mode, uninstall the app, and call your bank to block cards/UPI.",
    ],
    "url_apk": [
        "Don't install the file. If you did: turn on airplane mode, uninstall the app, and call your bank to block cards/UPI.",
    ],
    "url_lookalike": ["Don't enter anything on that site. Type the organisation's real address yourself instead."],
}

LOW_RISK_STEPS = [
    "No common scam signs were found, but that is not a guarantee.",
    "If it asks for money, an OTP or a download later on, check again before acting.",
]


def build_actions(verdict: str, signal_ids: list[str]) -> list[str]:
    if verdict == "low":
        return LOW_RISK_STEPS
    steps: list[str] = ["Don't click links, pay, or reply to this message."]
    for sid in signal_ids:
        for step in SPECIFIC.get(sid, []):
            if step not in steps:
                steps.append(step)
    if verdict == "scam":
        steps.extend(REPORT_STEPS)
    else:
        steps.append("Verify through an official channel you find yourself, not one given in the message.")
        steps.append(REPORT_STEPS[0])
    return steps[:7]
