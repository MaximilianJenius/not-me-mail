"""Scores an email on how likely it was meant for someone else.

Pure functions only - no network, no IMAP - so it can be unit tested with
raw .eml bytes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html import unescape

# Words that follow "Hi"/"Dear" but are not a person's name.
GENERIC_GREETING_WORDS = {
    "there", "all", "everyone", "everybody", "friend", "friends", "customer",
    "customers", "member", "members", "user", "users", "valued", "sir",
    "madam", "team", "folks", "guys", "you", "again", "shopper", "subscriber",
    "traveler", "traveller", "reader", "partner", "colleague", "client",
    "guest", "player", "fan", "investor", "from", "and", "to",
    "welcome", "hello", "hi", "hey", "dear", "thanks", "thank", "good",
    "morning", "afternoon", "evening", "the", "our", "your", "new", "back",
    "sg", "singapore", "account", "holder", "applicant",
}

GREETING_RE = re.compile(
    r"(?:^|[\n\r.!?>]\s*)"
    r"(?i:hi|hello|hey|dear|hiya|greetings|welcome(?: back)?|good (?:morning|afternoon|evening)"
    r"|congratulations|congrats|thanks|thank you)"
    r"[ \t]*,?[ \t]+"
    r"(?P<name>[A-Z][A-Za-z'\-]{1,30})"
    r"(?:[ \t]+(?P<last>[A-Z][A-Za-z'\-]{1,30}))?"
    r"[ \t]*[,!:.\n\r]",
)

# "John, your order has shipped" style subjects.
SUBJECT_NAME_RE = re.compile(r"^(?:re:\s*|fwd?:\s*)*(?P<name>[A-Z][a-z'\-]{1,30}),\s+\S")

SIGNUP_PATTERNS = [
    r"welcome to\b",
    r"verify your (?:e-?mail|account|address)",
    r"confirm your (?:e-?mail|account|address|registration|subscription|sign[- ]?up)",
    r"activate your account",
    r"thanks? (?:you )?for (?:signing up|registering|joining|creating an account|subscribing)",
    r"your (?:new )?account (?:has been|was|is) (?:created|registered|set up|ready)",
    r"complete your (?:registration|sign[- ]?up|profile)",
    r"one more step",
    r"(?:order|booking|reservation|purchase) (?:confirmation|confirmed|received|#)",
    r"your (?:order|booking|receipt|invoice)\b",
    r"reset your password|password reset",
    r"verification code|one[- ]time (?:code|password|passcode)|\botp\b",
    r"you(?:'ve| have) (?:successfully )?(?:subscribed|registered|signed up)",
]
SIGNUP_RE = re.compile("|".join(SIGNUP_PATTERNS), re.IGNORECASE)

# Header fingerprints left by bulk / marketing email services.
ESP_HEADER_HINTS = {
    "x-mailgun-sid": "Mailgun",
    "x-sg-eid": "SendGrid",
    "x-sg-id": "SendGrid",
    "x-mc-user": "Mailchimp",
    "x-mailchimp-campaign": "Mailchimp",
    "x-campaign": "campaign tool",
    "x-campaignid": "campaign tool",
    "x-ses-outgoing": "Amazon SES",
    "x-sfmc-stack": "Salesforce Marketing Cloud",
    "x-klaviyo-message-id": "Klaviyo",
    "x-brevo-id": "Brevo",
    "x-sib-id": "Brevo",
    "x-hubspot-mail-id": "HubSpot",
    "x-marketo-id": "Marketo",
    "x-mailer-sid": "bulk mailer",
    "feedback-id": "bulk mailer",
}

WEIGHTS = {
    "wrong_name": 3,
    "signup_unknown_service": 3,
    "not_in_to_cc": 1,
    "bulk": 1,
}
LIKELY_THRESHOLD = 3
POSSIBLE_THRESHOLD = 2


@dataclass
class Profile:
    """Who the mailbox owner is - used to tell 'me' from 'someone else'."""

    my_addresses: list[str]
    my_names: list[str]
    known_service_domains: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.my_addresses = [a.strip().lower() for a in self.my_addresses if a.strip()]
        names: set[str] = set()
        for n in self.my_names:
            for part in re.split(r"[\s.\-_]+", n.strip().lower()):
                if part:
                    names.add(part)
        self.name_tokens = names
        self.known_service_domains = [
            d.strip().lower().lstrip("@") for d in self.known_service_domains if d.strip()
        ]

    def is_my_address(self, addr: str) -> bool:
        addr = addr.strip().lower()
        if addr in self.my_addresses:
            return True
        # Plus-addressing: me+shop@x.com
        local, _, domain = addr.partition("@")
        base = local.split("+", 1)[0] + "@" + domain
        return base in self.my_addresses

    def is_my_name(self, name: str) -> bool:
        return name.strip().lower() in self.name_tokens

    def is_known_service(self, domain: str) -> bool:
        domain = domain.lower()
        return any(domain == d or domain.endswith("." + d) for d in self.known_service_domains)


@dataclass
class Verdict:
    uid: str
    date: str
    from_name: str
    from_addr: str
    sender_domain: str
    to: str
    subject: str
    score: int
    level: str  # "likely" | "possible" | "clean"
    reasons: list[str]
    names_seen: list[str]
    unsubscribe: str

    def to_dict(self) -> dict:
        return asdict(self)


def _domain_of(addr: str) -> str:
    return addr.rpartition("@")[2].lower()


def _root_domain(domain: str) -> str:
    parts = domain.split(".")
    # Good enough for e.g. mail.shop.com.sg -> shop.com.sg
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in {"com", "co", "net", "org", "gov", "edu"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</td>", "\n", html)
    html = re.sub(r"<[^>]+>", " ", html)
    text = unescape(html)
    text = re.sub(r"[ \t ‌​]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text)


def body_text(msg: EmailMessage, limit: int = 6000) -> str:
    """First `limit` chars of readable body text (plain preferred, else HTML)."""
    plain, html = None, None
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:  # broken charset / truncated part
            payload = part.get_payload(decode=True) or b""
            content = payload.decode("utf-8", errors="replace")
        if not isinstance(content, str):
            continue
        if ctype == "text/plain" and plain is None:
            plain = content
        elif ctype == "text/html" and html is None:
            html = content
    text = plain if plain and plain.strip() else (_html_to_text(html) if html else "")
    return text[:limit]


def _first_unsubscribe_url(header: str) -> str:
    for target in re.findall(r"<([^>]+)>", header or ""):
        if target.lower().startswith("http"):
            return target
    for target in re.findall(r"<([^>]+)>", header or ""):
        return target
    return ""


def find_greeting_names(text: str) -> list[str]:
    names = []
    for m in GREETING_RE.finditer(text):
        first = m.group("name")
        if first.lower() in GENERIC_GREETING_WORDS:
            continue
        names.append(first)
    return names


def analyze(uid: str, raw: bytes, profile: Profile) -> Verdict:
    msg: EmailMessage = message_from_bytes(raw, policy=policy.default)  # type: ignore[assignment]

    def header(name: str) -> str:
        try:
            return str(msg.get(name, "") or "")
        except Exception:
            return ""

    subject = header("Subject").strip()
    from_name, from_addr = parseaddr(header("From"))
    from_addr = from_addr.lower()
    sender_domain = _root_domain(_domain_of(from_addr)) if from_addr else ""

    to_pairs = getaddresses([header("To")])
    cc_pairs = getaddresses([header("Cc")])
    recipients = to_pairs + cc_pairs

    reasons: list[str] = []
    names_seen: list[str] = []
    score = 0

    # 1. Not addressed to me in To/Cc (arrived via BCC, list, or forwarding).
    my_recipients = [(n, a) for n, a in recipients if a and profile.is_my_address(a)]
    if recipients and not my_recipients:
        score += WEIGHTS["not_in_to_cc"]
        shown = ", ".join(a for _, a in recipients if a)[:80] or "undisclosed recipients"
        reasons.append(f"Your address isn't in To/Cc (sent to: {shown})")
    elif not recipients:
        score += WEIGHTS["not_in_to_cc"]
        reasons.append("No To/Cc header - sent as BCC")

    # 2. Wrong name: in the To display name, subject line, or body greeting.
    wrong: list[str] = []
    for disp, addr in my_recipients:
        if disp:
            first = re.split(r"[\s,]+", disp.strip().strip('"'))[0]
            if first and "@" not in first and first.lower() not in GENERIC_GREETING_WORDS \
                    and not profile.is_my_name(first):
                wrong.append(first)
                reasons.append(f'Addressed to "{disp}" in the To field')
    m = SUBJECT_NAME_RE.match(subject)
    if m and m.group("name").lower() not in GENERIC_GREETING_WORDS and not profile.is_my_name(m.group("name")):
        wrong.append(m.group("name"))
        reasons.append(f'Subject uses the name "{m.group("name")}"')

    text = body_text(msg)
    for name in find_greeting_names(text):
        names_seen.append(name)
        if not profile.is_my_name(name):
            if name not in wrong:
                reasons.append(f'Greets "{name}" in the body')
            wrong.append(name)
    names_seen = sorted(set(names_seen + wrong), key=str.lower)
    if wrong and profile.name_tokens:
        score += WEIGHTS["wrong_name"]

    # 3. Sign-up / order / verification mail from a service you don't use.
    haystack = subject + "\n" + text[:2500]
    sm = SIGNUP_RE.search(haystack)
    if sm and not profile.is_known_service(sender_domain or _domain_of(from_addr)):
        score += WEIGHTS["signup_unknown_service"]
        reasons.append(f'Account/order email ("{sm.group(0).strip()}") from {sender_domain or "unknown sender"}, '
                       f"which isn't in your known services")

    # 4. Bulk / marketing headers.
    bulk_hints = []
    unsub_header = header("List-Unsubscribe")
    if unsub_header:
        bulk_hints.append("List-Unsubscribe")
    if header("List-Id"):
        bulk_hints.append("List-Id")
    if header("Precedence").lower() in {"bulk", "list", "junk"}:
        bulk_hints.append(f"Precedence: {header('Precedence').lower()}")
    keys = {k.lower() for k in msg.keys()}
    for k, label in ESP_HEADER_HINTS.items():
        if k in keys and label not in bulk_hints:
            bulk_hints.append(label)
    if bulk_hints:
        score += WEIGHTS["bulk"]
        reasons.append("Bulk/marketing mail (" + ", ".join(bulk_hints[:3]) + ")")

    if score >= LIKELY_THRESHOLD:
        level = "likely"
    elif score >= POSSIBLE_THRESHOLD:
        level = "possible"
    else:
        level = "clean"

    try:
        date = parsedate_to_datetime(header("Date")).isoformat()
    except Exception:
        date = header("Date")

    return Verdict(
        uid=uid,
        date=date,
        from_name=from_name,
        from_addr=from_addr,
        sender_domain=sender_domain,
        to=header("To")[:200],
        subject=subject[:300],
        score=score,
        level=level,
        reasons=reasons,
        names_seen=names_seen,
        unsubscribe=_first_unsubscribe_url(unsub_header),
    )
