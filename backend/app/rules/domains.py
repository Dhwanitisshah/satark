"""Reference lists for link, UPI and brand checks. Extend freely — these are data, not logic."""

APK_RE = r"\b[\w\s-]{0,40}\.apk\b"

SHORTENERS = {
    "bit.ly", "tinyurl.com", "cutt.ly", "rb.gy", "t.ly", "is.gd", "shorturl.at", "tiny.cc",
    "s.id", "goo.su", "ow.ly", "rebrand.ly", "shorturl.gg", "v.gd", "bit.do", "t.co",
}

SUSPICIOUS_TLDS = {
    "xyz", "top", "click", "buzz", "live", "shop", "online", "site", "icu", "info", "rest",
    "cfd", "sbs", "lol", "vip", "cc", "tk", "ml", "ga", "cf", "gq", "work", "support", "cyou",
    "monster", "pw", "bond", "store", "fun", "win", "loan",
}

KNOWN_TLDS = SUSPICIOUS_TLDS | {
    "com", "in", "net", "org", "co", "io", "me", "app", "dev", "gov", "edu", "sbi", "bank", "ly", "gl", "gd",
    "at", "id", "su", "gg", "do", "us", "uk", "ai",
}

# Second-level suffixes where the registrable domain has three labels.
MULTI_PART_SUFFIXES = {
    "co.in", "org.in", "net.in", "gov.in", "nic.in", "ac.in", "edu.in", "res.in", "bank.in",
    "firm.in", "gen.in", "ind.in", "co.uk", "org.uk",
}

# Only government / RBI-controlled registrars can issue these, so anything under them is trusted.
TRUSTED_SUFFIXES = {"gov.in", "nic.in", "bank.in"}

OFFICIAL_DOMAINS = {
    "onlinesbi.sbi", "sbi.co.in", "onlinesbi.com", "sbicard.com",
    "hdfcbank.com", "icicibank.com", "axisbank.com", "kotak.com", "pnbindia.in", "bankofbaroda.in",
    "paytm.com", "phonepe.com", "pay.google.com", "google.com", "npci.org.in", "rbi.org.in",
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "irctc.co.in",
    "fedex.com", "dhl.com", "bluedart.com", "indiapost.gov.in",
    "mahadiscom.in", "tatapower.com", "adanielectricity.com", "bescom.co.in",
    "whatsapp.com", "youtube.com", "telegram.org",
}

# Brand names scammers put inside fake domains (sbi-kyc-update.xyz, hdfc-rewards.top ...).
BRAND_TOKENS = [
    "sbi", "yono", "hdfc", "icici", "axis", "kotak", "pnb", "paytm", "phonepe", "gpay", "bhim", "npci",
    "rbi", "amazon", "flipkart", "irctc", "indiapost", "fedex", "dhl", "bluedart", "incometax", "epfo",
    "uidai", "aadhaar", "trai", "msedcl", "mahadiscom", "bescom", "customs", "police", "kyc",
]

UPI_PSP_HANDLES = {
    "okaxis", "okhdfcbank", "okicici", "oksbi", "ybl", "ibl", "axl", "paytm", "ptyes", "ptaxis",
    "pthdfc", "ptsbi", "upi", "apl", "yapl", "axisbank", "icici", "sbi", "hdfcbank", "kotak",
    "axisb", "idfcbank", "freecharge", "ikwik", "airtel", "jio", "waaxis", "wahdfcbank", "wasbi",
    "waicici", "fbl", "rbl", "yesbank", "indus", "pnb", "barodampay", "unionbank", "kbl", "superyes",
}


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 without a network call (good enough for scam checks)."""
    parts = host.lower().strip(".").split(".")
    if len(parts) <= 2:
        return ".".join(parts)
    last_two = ".".join(parts[-2:])
    if last_two in MULTI_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return last_two
