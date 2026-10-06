"""Deterministic scam-signal engine.

Every signal carries the exact text it matched (``evidence``) so the UI can
highlight it and the verdict never rests on an LLM guess alone.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import asdict, dataclass, field

from .domains import (
    APK_RE,
    BRAND_TOKENS,
    OFFICIAL_DOMAINS,
    SHORTENERS,
    SUSPICIOUS_TLDS,
    TRUSTED_SUFFIXES,
    KNOWN_TLDS,
    UPI_PSP_HANDLES,
    registrable_domain,
)


@dataclass
class Signal:
    id: str
    category: str
    label: str
    why: str
    weight: int
    evidence: str = ""
    # Values that were spliced into the English label/why (domain, brand, ...), so a translation can reuse them.
    params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Pattern:
    id: str
    category: str
    label: str
    why: str
    weight: int
    regex: str
    negatable: bool = False  # drop matches preceded by "do not / never / don't"
    skip_near: str = ""  # drop matches when this regex hits the match or the 30 characters before it


I = re.IGNORECASE

PATTERNS: list[Pattern] = [
    Pattern(
        "digital_arrest", "impersonation", "Fake police / CBI / 'digital arrest'",
        "No Indian agency arrests anyone over a video call or asks for money to 'clear your name'. "
        "'Digital arrest' does not exist in law.",
        40,
        r"digital(?:ly)?\s+arrest|\bcbi\b|\bncb\b|narcotics|money\s+laundering|arrest\s+warrant|"
        r"cyber\s*crime\s+(?:department|cell|branch)|giraftar|गिरफ्तार|डिजिटल\s*अरेस्ट",
    ),
    Pattern(
        "courier_parcel", "impersonation", "Seized parcel / customs lure",
        "Courier companies and customs don't call to say your parcel has drugs and then ask you to pay.",
        25,
        r"(?:parcel|courier|fedex|dhl|blue\s*dart|customs|package|पार्सल).{0,80}?"
        r"(?:seized|drugs|illegal|contraband|held\s+at|on\s+hold|returned|pakda|pakde|jabt|ड्रग्स|जब्त)",
    ),
    Pattern(
        "kyc_block", "account_threat", "Account / KYC block threat",
        "Banks don't block accounts over SMS links. KYC is updated in the official app or at a branch.",
        25,
        r"kyc.{0,40}?(?:expir|updat|block|suspend|pending)|"
        r"(?:account|card|sim|yono|wallet).{0,30}?(?:will\s+be\s+)?(?:blocked|suspended|deactivated|closed)|"
        r"(?:account|card|khata|sim|yono|wallet).{0,30}?(?:band|block)\s+ho\s+ja(?:yega|yegi|ega|egi)|"
        r"खाता\s*बंद",
    ),
    Pattern(
        "electricity", "account_threat", "Electricity disconnection threat",
        "Power companies send official bills; they don't ask you to call a personal mobile number tonight.",
        25,
        r"electricity.{0,60}?(?:disconnect|cut)|power.{0,20}?(?:will\s+be\s+)?(?:cut|disconnected)|"
        # Hinglish / Hindi / Marathi: "bijli ... connection kat diya jayega", "वीज कनेक्शन कापले जाईल"
        r"(?:bijli|बिजली|वीज).{0,120}?(?:\bkaa?t\b|कट\s*(?:जाएगा|जाएगी|दिया)|कापले|काटा\s+जाएगा|disconnect)",
    ),
    Pattern(
        "lottery_prize", "too_good", "Prize / lottery / cashback you didn't enter",
        "You can't win a lottery or cashback you never entered. 'Claim' steps always end in a payment or PIN.",
        25,
        r"(?:\bwon\b|winner|lottery|lucky\s+draw|\bprize\b|cashback|\bkbc\b|इनाम|inaam|jackpot).{0,60}?"
        r"(?:rs\.?|₹|inr|lakh|crore|iphone|\bcar\b|reward)|"
        r"(?:₹|rs\.?|inr)\s?[\d,]+\s*(?:lakh|crore)?\s*(?:ka|ki|ke)?\s*(?:inaam|inam|इनाम|prize|lottery|jackpot)",
    ),
    Pattern(
        "job_task", "too_good", "Part-time 'task' job",
        "Paying people to like videos or rate hotels is the opening move of task scams: small payouts, then a 'deposit' to unlock more.",
        25,
        r"work\s+from\s+home|part[\s-]?time\s+job|daily\s+(?:income|earning)|like\s+(?:youtube\s+)?videos|"
        r"rate\s+(?:hotels|products|restaurants)|prepaid\s+task|telegram\s+task|"
        r"ghar\s+baithe|घर\s+बैठे|part[\s-]?time\s+kaam|videos?\s+like\s+kar",
    ),
    Pattern(
        "earnings", "too_good", "Unrealistic earnings promise",
        "Specific daily-earning promises with no experience needed are a recruitment hook.",
        20,
        r"earn.{0,25}?(?:₹|rs\.?|inr)\s?\d|(?:roz|rozana|रोज़?)\s*(?:₹|rs\.?|inr)\s?\d",
    ),
    Pattern(
        "investment", "too_good", "Guaranteed-returns investment",
        "No legitimate investment guarantees returns. SEBI-registered advisers don't run WhatsApp 'VIP groups'.",
        25,
        r"(?:guaranteed|assured|fixed|double).{0,30}?(?:returns?|profits?|money)|"
        r"(?:vip|stock|trading)\s+(?:tips|group)|crypto.{0,30}?(?:profit|return)",
    ),
    Pattern(
        "otp_request", "credential", "Asks for OTP / PIN / CVV",
        "No bank, app or official ever needs your OTP, PIN or CVV. Sharing it hands over your account.",
        50,
        r"(?:share|send|tell|forward|provide|give).{0,30}?\b(?:otp|pin|cvv|password)\b|"
        r"\b(?:otp|cvv)\b.{0,30}?(?:share|send|tell|forward)|"
        # Hinglish: "OTP bata dijiye", "OTP bhej do"
        r"\botp\b.{0,30}?(?:bata|batao|bhej|bhejo|de\s*do|dijiye)",
        negatable=True,
    ),
    Pattern(
        "remote_access", "credential", "Asks you to install a screen-sharing app",
        "AnyDesk / TeamViewer give a stranger full control of your phone, including your OTPs.",
        50,
        r"anydesk|teamviewer|quick\s*support|rustdesk|screen\s*shar",
        negatable=True,
    ),
    Pattern(
        "upi_receive", "payment", "'Enter PIN / scan QR to receive money'",
        "You never scan a QR code or enter your UPI PIN to RECEIVE money. A PIN only ever sends money out.",
        50,
        r"(?:scan|qr).{0,40}?(?:receive|get|claim)\s+(?:the\s+)?(?:money|payment|refund|cashback|amount)|"
        r"(?:enter|use)\s+(?:your\s+)?(?:upi\s+)?pin\s+to\s+(?:receive|get|claim)|"
        r"accept\s+(?:the\s+)?collect\s+request",
    ),
    Pattern(
        "payment_demand", "payment", "Asks you to pay or transfer money",
        "A message that ends in 'pay now' deserves a second check through an official channel.",
        15,
        r"(?:pay|transfer|deposit|send|need).{0,40}?(?:₹|rs\.?\s?\d|inr|\bfee\b|charges?|\bfine\b|penalty|"
        r"processing|security\s+deposit)|"
        # Hinglish / Hindi, amount first: "₹5000 fees bhejein", "₹20,000 भेजो"
        r"(?:₹|rs\.?|inr)\s?\d[\d,]*\s*(?:\w+\s+){0,2}?(?:bhej|jama|भेज|जमा)",
    ),
    Pattern(
        "advance_fee", "payment", "Asks for a fee up front",
        "Genuine prizes, loans and jobs never ask you to pay first to receive them.",
        20,
        r"(?:pehle|advance|upfront|first)\s+(?:\w+\s+){0,3}?(?:₹|rs\.?|inr)\s?\d[\d,]*\s*(?:as\s+)?"
        r"(?:fees?|charges?|deposit|tax|registration|processing)|"
        r"(?:advance|upfront)\s+(?:fee|payment|charges?|deposit)",
    ),
    Pattern(
        "job_fee", "too_good", "Job offer that costs money",
        "Real employers never charge a deposit or fee to confirm a job, an offer letter or joining.",
        35,
        r"(?:shortlisted|selected|offer\s+letter|joining|job\s+confirmation).{0,80}?(?:deposit|\bfee\b|charges)|"
        r"(?:deposit|\bfee\b|charges).{0,60}?(?:offer\s+letter|confirm\s+your\s+(?:job|selection|offer))",
    ),
    Pattern(
        "urgency", "pressure", "Artificial urgency",
        "Pressure to act within minutes is designed to stop you from checking with someone.",
        10,
        r"\burgent(?:ly)?\b|immediately|within\s+\d+\s*(?:hours?|hrs?|minutes?|mins?)|"
        r"\d+\s*(?:minutes?|mins?)\s+only|today\s+itself|tonight|expires?\s+today|last\s+(?:chance|warning)|"
        r"final\s+notice|act\s+now|limited\s+(?:seats|time|offer)|seats?\s+(?:are\s+)?limited|"
        r"तुरंत|turant|foran|aaj\s+raat|aaj\s+hi|ताबडतोब|आज\s+रात्री|आज\s+रात",
    ),
    Pattern(
        "secrecy", "pressure", "Asks you to keep it secret",
        "Scammers isolate victims. Anyone telling you not to tell family or police is the threat.",
        25,
        r"(?:don'?t|do\s+not|never)\s+(?:tell|inform|share\s+(?:this\s+)?with|disconnect)"
        r"(?:\s+(?:the|this))?\s*(?:anyone|family|police|mummy|papa|mom|dad|parents|video\s+call|call)|"
        r"keep\s+(?:this\s+)?(?:confidential|secret)|"
        # Hinglish / Hindi: "kisi ko mat batana", "mummy ko mat batana", "किसी को न बताएं", "मम्मी को मत बताना"
        r"(?:kisi|kisiko|ghar\s+walon?|mummy|papa|police|family)\s+(?:ko\s+)?(?:mat|na)\s+bata\w*|"
        r"(?:किसी|मम्मी|पापा|पुलिस|घर\s+वालों?)\s*(?:को\s+)?(?:मत|न)\s+बता",
        # "OTP kisi ko mat batana" is a bank's warning, not a scammer's secrecy demand
        skip_near=r"\botp\b|\bpin\b|\bcvv\b|password|ओटीपी|पासवर्ड",
    ),
    Pattern(
        "family_emergency", "impersonation", "'New number' family emergency",
        "Voice clones and 'new number' messages fake a relative in trouble. Call them back on their old number.",
        25,
        r"(?:this\s+is\s+)?my\s+new\s+number|(?:accident|hospital|police\s+station).{0,60}?(?:send|need|transfer)|"
        r"(?:mera|ye\s+mera|yeh\s+mera)\s+naya\s+number|naya\s+number|(?:मेरा|ये\s+मेरा)\s+नया\s+(?:नंबर|नम्बर)|"
        r"नया\s+(?:नंबर|नम्बर)",
    ),
    Pattern(
        "callback", "pressure", "Pushes you to call a personal number",
        "Official bodies give you a published helpline, not a 10-digit mobile number in an SMS.",
        15,
        r"(?:call|contact|whatsapp).{0,40}?(?:\+?91[\s-]?)?[6-9]\d{9}\b|"
        r"[6-9]\d{9}\b.{0,20}?(?:संपर्क|कॉल|call|contact|whatsapp)",
    ),
    Pattern(
        "off_platform", "pressure", "Moves you to Telegram / WhatsApp",
        "Moving the conversation to Telegram or a private WhatsApp chat removes platform protections.",
        10,
        r"telegram|t\.me/|wa\.me/",
    ),
    Pattern(
        "apk", "malware", "Asks you to install an .apk file",
        "APK files sent over chat are banking malware: they read your SMS and OTPs. Apps come only from the Play Store.",
        50,
        APK_RE,
    ),
]

# "never share", "do not click links or install ..." — a warning, not a request. Up to five words may sit
# between the negator and the verb, but punctuation breaks the link ("Don't worry, just install" is not negated).
_NEGATION = re.compile(
    r"\b(?:never|do\s+not|don'?t|not\s+to|mat|na|nahi|nahin)\s+(?:\w+\s+){0,5}?"
    r"(?:share|send|tell|give|provide|forward|install|download|disclose|ask|bata\w*|bhej\w*)", I)
_NEGATION_WINDOW = 70

# A message that itself carries an OTP and tells you to give it to the delivery agent is a genuine
# delivery notice (nobody phishing you can include your real code), so it isn't an OTP *request*.
_DELIVERY_AGENT = re.compile(r"(?:delivery|courier)\s+(?:agent|partner|executive|person|boy)", I)
_CODE = re.compile(r"\b\d{4,6}\b")


def _is_delivery_otp(text: str) -> bool:
    return bool(_DELIVERY_AGENT.search(text) and _CODE.search(text))

URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"')]+", I)
BARE_HOST_RE = re.compile(r"(?<![@\w.])(?:[a-z0-9-]+\.)+[a-z]{2,24}(?:/[^\s<>\"')]*)?", I)
UPI_RE = re.compile(r"\b([a-z0-9][a-z0-9._-]{1,63})@([a-z]{2,32})\b(?!\.)", I)
INTL_PHONE_RE = re.compile(r"\+(\d{1,3})(?:[\s-]?\d{2,5}){2,4}")  # "+84 912 345 678", "+92 300 1234567"
SCAM_COUNTRY_CODES = {"92", "84", "62", "60", "880", "855", "856", "95", "234", "63"}
_SCAM_SUFFIX = r"(?:kyc|update|rewards?|login|verify|refund|online|secure|care|support|help|pay|points|bonus)"
UPI_IMPERSONATION_WORDS = re.compile(
    r"refund|support|care|helpdesk|kyc|police|govt|gov|customs|rbi|sbi|income|tax|fine|cashback", I
)


def _match_patterns(text: str) -> list[Signal]:
    found: list[Signal] = []
    for p in PATTERNS:
        if p.id == "otp_request" and _is_delivery_otp(text):
            continue
        for m in re.finditer(p.regex, text, I):
            if p.negatable and _NEGATION.search(text[max(0, m.start() - _NEGATION_WINDOW): m.end()]):
                continue
            if p.skip_near and re.search(p.skip_near, text[max(0, m.start() - 30): m.end()], I):
                continue
            found.append(Signal(p.id, p.category, p.label, p.why, p.weight, m.group(0).strip()))
            break  # one hit per pattern is enough
    return found


def extract_urls(text: str) -> list[str]:
    urls = [u.rstrip(".,;:!?") for u in URL_RE.findall(text)]
    seen = {u.lower() for u in urls}
    for m in BARE_HOST_RE.finditer(text):
        cand = m.group(0).rstrip(".,;:!?")
        host = cand.split("/")[0].lower()
        tld = host.rsplit(".", 1)[-1]
        if tld not in KNOWN_TLDS:
            continue
        if any(host in u for u in seen):
            continue
        urls.append(cand)
        seen.add(cand.lower())
    return urls


def _host(url: str) -> str:
    u = re.sub(r"^https?://", "", url, flags=I)
    return u.split("/")[0].split("?")[0].split(":")[0].lower()


def analyse_url(url: str) -> list[Signal]:
    host = _host(url)
    sigs: list[Signal] = []
    reg = registrable_domain(host)
    tld = host.rsplit(".", 1)[-1]

    try:
        ipaddress.ip_address(host)
        sigs.append(Signal("url_ip", "link", "Link points to a raw IP address",
                           "Real organisations use named domains, not bare IP addresses.", 25, url))
    except ValueError:
        pass
    if "xn--" in host:
        sigs.append(Signal("url_punycode", "link", "Look-alike characters in link",
                           "This domain uses lookalike (punycode) characters to imitate a real site.", 25, url))
    if reg in SHORTENERS:
        sigs.append(Signal("url_shortener", "link", "Shortened link hides the real destination",
                           "Short links hide where they lead. Banks and government sites don't use them in alerts.",
                           15, url))
    if tld in SUSPICIOUS_TLDS:
        sigs.append(Signal("url_tld", "link", f"Cheap throwaway domain ending (.{tld})",
                           f".{tld} domains are cheap and widely used for phishing pages.", 20, url,
                           params={"tld": tld}))
    trusted = reg in OFFICIAL_DOMAINS or any(reg.endswith("." + s) or reg == s for s in TRUSTED_SUFFIXES)
    if not trusted:
        label = host.replace(reg, "") + reg.split(".")[0]
        for brand in BRAND_TOKENS:
            if re.search(rf"(?:^|[.\-_0-9]){brand}(?:[.\-_0-9]|$)|{brand}{_SCAM_SUFFIX}", label):
                sigs.append(Signal("url_lookalike", "link", f"Fake '{brand.upper()}' look-alike website",
                                   f"The link uses the name '{brand}' but is not that organisation's official domain "
                                   f"(it's really {reg}).", 35, url,
                                   params={"brand": brand.upper(), "brand_l": brand, "reg": reg}))
                break
    if url.lower().split("?")[0].endswith(".apk"):
        sigs.append(Signal("url_apk", "malware", "Link downloads an .apk app",
                           "Installing apps from links lets malware read your SMS and OTPs.", 50, url))
    return sigs


def analyse_upi(text: str, has_payment_context: bool) -> list[Signal]:
    sigs: list[Signal] = []
    for m in UPI_RE.finditer(text):
        name, psp = m.group(1).lower(), m.group(2).lower()
        handle = m.group(0)
        if psp not in UPI_PSP_HANDLES:
            continue  # probably an email-like string, not a UPI id
        if UPI_IMPERSONATION_WORDS.search(name):
            sigs.append(Signal("upi_impersonation", "payment", "Official-sounding personal UPI ID",
                               "Government bodies and companies never collect fines or fees through a personal UPI ID.",
                               30, handle))
        elif has_payment_context:
            sigs.append(Signal("upi_target", "payment", "Asks you to pay a personal UPI ID",
                               "Money sent by UPI is almost impossible to reverse. Verify the person by calling them.",
                               15, handle))
        break
    return sigs


def analyse_phones(text: str) -> list[Signal]:
    for m in INTL_PHONE_RE.finditer(text):
        cc = m.group(1)
        for k in (cc[:3], cc[:2], cc[:1]):
            if k in SCAM_COUNTRY_CODES:
                return [Signal("foreign_number", "impersonation", f"Foreign number (+{k}) claiming to be local",
                               "Indian police, banks and companies don't contact you from foreign WhatsApp numbers.",
                               25, m.group(0), params={"k": k})]
    return []


def run_rules(text: str) -> dict:
    """Return all signals plus a 0-100 risk score from the rules alone."""
    text = text or ""
    signals = _match_patterns(text)
    for url in extract_urls(text):
        signals.extend(analyse_url(url))
    pay_ctx = any(s.category == "payment" for s in signals)
    signals.extend(analyse_upi(text, pay_ctx))
    signals.extend(analyse_phones(text))

    # de-duplicate by id (a URL and the text can both flag .apk)
    uniq: dict[str, Signal] = {}
    for s in signals:
        if s.id == "url_apk" and "apk" in uniq:
            continue
        uniq.setdefault(s.id, s)
    signals = sorted(uniq.values(), key=lambda s: -s.weight)
    score = min(100, sum(s.weight for s in signals))
    primary = signals[0].id if signals else None

    return {"score": score, "signals": [s.to_dict() for s in signals], "primary": primary}
