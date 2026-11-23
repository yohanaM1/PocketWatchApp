"""Rule-based cleanup of bank descriptions into a merchant name.

    "SQ *STARBUCKS #2411 HYATTSVILLE MD"  ->  "STARBUCKS"
    "AMZN Mktp US*2K4L1"                 ->  "AMAZON"

This is a deliberately simple first version: category corrections are keyed on the
merchant name, so it only needs to be consistent for the same merchant. Known gaps:
city names without a store number stay attached ("CHIPOTLE MEXICAN GRILL COLLEGE PARK"),
and unfamiliar payment-processor prefixes aren't recognized. The LLM categorization
pipeline may refine merchants later.

Names are stored at import time. Any rule change here needs a data migration that re-runs the
rules over existing rows (see app/services/merchant_backfill.py), or old and new imports of the
same merchant get different names and saved corrections stop matching.
"""

import re

MAX_MERCHANT_LENGTH = 100

# Well-known merchants whose bank descriptions vary a lot. Checked first, in order.
ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(p), name)
    for p, name in [
        (r"^UBER\s*\*\s*EATS\b", "UBER EATS"),
        (r"^UBER\b", "UBER"),
        (r"^LYFT\b", "LYFT"),
        (r"^(AMZN|AMAZON)\b", "AMAZON"),
        (r"^(DOORDASH|DD\s*\*\s*DOORDASH)\b", "DOORDASH"),
        (r"^VENMO\b", "VENMO"),
        (r"^ZELLE\b", "ZELLE"),
        (r"^APPLE\.COM/BILL\b", "APPLE"),
        (r"^(WAL-MART|WALMART|WM SUPERCENTER)\b", "WALMART"),
    ]
]

# Other spellings of a name prefix, rewritten before anything else ("UNIV OF MD DINING" -> "UMD DINING").
PREFIX_REWRITES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(UNIV OF MD|UNIVERSITY OF MARYLAND)\b"), "UMD"),
]

# Apostrophes, straight or curly: "TRADER JOE'S" -> "TRADER JOES".
APOSTROPHES = re.compile(r"['’]")

# Text before a "*" that is a payment processor, not the merchant ("SQ *STARBUCKS").
PROCESSOR_PREFIXES = {"SQ", "TST", "SP", "DD", "PP", "TM", "BZR", "PAYPAL", "PY", "IC"}

LEADING_NOISE = re.compile(r"^(INTL TXN|POS|DEBIT CARD PURCHASE|CHECKCARD|PURCHASE|RECURRING)\s+")

US_STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

DOMAIN = re.compile(r"^WWW\.|\.(COM|NET|ORG|CO)$")

# Banking words that trail the merchant name ("PEPCO BILL PAY", "LANDMARK RENT ACH").
TRAILING_NOISE = {"STORE", "ACH", "PPD", "ID", "WEB", "DIR", "DEP", "BILL", "PAY", "PAYMENT", "PURCHASE", "ONLINE", "AUTOLOAD"}


# Raw bank description -> merchant name
def normalize_merchant(description: str) -> str:
    """Turn a raw bank description into a short, consistent merchant name (see the module docstring)."""
    text = " ".join(description.upper().split())
    text = LEADING_NOISE.sub("", text)
    text = APOSTROPHES.sub("", text)
    for pattern, replacement in PREFIX_REWRITES:
        text = pattern.sub(replacement, text)

    for pattern, name in ALIASES:
        if pattern.search(text):
            return name

    if "*" in text:
        before, after = (part.strip() for part in text.split("*", 1))
        text = after if before in PROCESSOR_PREFIXES or not before else before

    tokens: list[str] = []
    for i, token in enumerate(text.replace("-", " ").split()):
        token = token.split("/")[0]  # "CVS/PHARMACY" -> "CVS"
        if i > 0:
            # Everything from the first store number / reference code / web address onward
            # is location or noise. (The first word may contain digits, e.g. "7 ELEVEN".)
            if any(c.isdigit() for c in token) or token.startswith("#") or DOMAIN.search(token):
                break
        token = DOMAIN.sub("", token)  # "NETFLIX.COM" -> "NETFLIX"
        token = token.strip(".,:;#")
        if token == "S" and tokens:
            # Banks often turn an apostrophe into a space: "TRADER JOE S" -> "TRADER JOES".
            tokens[-1] += "S"
            continue
        tokens.append(token)

    if len(tokens) > 1 and tokens[-1] in US_STATES:
        tokens.pop()
    while len(tokens) > 1 and (tokens[-1] in TRAILING_NOISE or len(tokens[-1]) == 1):
        tokens.pop()

    merchant = " ".join(t for t in tokens if t)
    if not merchant:  # e.g. the description starts with a number
        merchant = text.split()[0] if text.split() else description.strip().upper()
    return merchant[:MAX_MERCHANT_LENGTH]
