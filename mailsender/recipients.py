"""Turn a pasted blob (a harvester export, a WhatsApp forward, a spreadsheet column)
into a clean send list, with the reason for every address that was left out."""

import re
from dataclasses import dataclass

# Recruiters dodge scrapers with "name [at] company [dot] com"; only bracketed forms
# are rewritten, since a bare " at " also appears in "hiring at Noida".
_OBFUSCATIONS = [
    (re.compile(r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*", re.I), "@"),
    (re.compile(r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*", re.I), "."),
]
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_NOT_A_MAILBOX = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$|@\d+x\.", re.I)
_NO_REPLY = re.compile(r"^(no-?reply|do-?not-?reply|donotreply|notifications?|mailer-daemon|postmaster)@", re.I)

# Misspelt free-mail domains seen in real pasted lists. Held back, not "fixed":
# guessing wrong would mail a stranger.
TYPO_DOMAINS = {
    "gamil.com": "gmail.com", "gmial.com": "gmail.com", "gmai.com": "gmail.com", "gmil.com": "gmail.com",
    "gmail.co": "gmail.com", "gmail.con": "gmail.com", "gmail.cm": "gmail.com", "gnail.com": "gmail.com",
    "gmaill.com": "gmail.com", "yaho.com": "yahoo.com", "yahoo.con": "yahoo.com", "yahooo.com": "yahoo.com",
    "hotmial.com": "hotmail.com", "hotmai.com": "hotmail.com", "hotmail.con": "hotmail.com",
    "outlok.com": "outlook.com", "outllook.com": "outlook.com", "outlook.con": "outlook.com",
    "rediffmai.com": "rediffmail.com", "redifmail.com": "rediffmail.com",
}


def find_emails(text: str) -> list[str]:
    """Every address in the text, lower-cased, in order, duplicates included."""
    for pattern, repl in _OBFUSCATIONS:
        text = pattern.sub(repl, text)
    out = []
    for raw in EMAIL_RE.findall(text):
        email = raw.strip(".").lower()
        if not _NOT_A_MAILBOX.search(email):
            out.append(email)
    return out


@dataclass
class Checked:
    send: list[str]                      # the final list, in the order pasted
    skipped: list[tuple[str, str]]       # (address, reason)
    found: int                           # addresses in the paste, duplicates included

    def summary(self) -> dict:
        reasons: dict[str, int] = {}
        for _, why in self.skipped:
            key = why.split(":")[0]
            reasons[key] = reasons.get(key, 0) + 1
        return {"found": self.found, "send": len(self.send), "skipped": reasons}


def check(text: str, recently_sent: dict[str, str] | None = None, blocked: set[str] | None = None,
          rejected: dict[str, str] | None = None) -> Checked:
    """`recently_sent` maps address -> when it was last mailed (already cut to the cooldown);
    `rejected` maps address -> why a server refused it for good."""
    recently_sent, rejected = recently_sent or {}, rejected or {}
    blocked = {b.lower().lstrip("@") for b in (blocked or set())}
    emails = find_emails(text)
    send, skipped, seen = [], [], set()
    for e in emails:
        if e in seen:
            skipped.append((e, "duplicate in the list"))
            continue
        seen.add(e)
        domain = e.rsplit("@", 1)[1]
        if _NO_REPLY.match(e):
            skipped.append((e, "no-reply address: nobody reads it"))
        elif domain in TYPO_DOMAINS:
            skipped.append((e, f"looks like a typo: did you mean {e.split('@')[0]}@{TYPO_DOMAINS[domain]}?"))
        elif e in blocked or domain in blocked:
            skipped.append((e, "on your skip list"))
        elif e in rejected:
            skipped.append((e, f"refused before: {rejected[e][:80]}"))
        elif e in recently_sent:
            skipped.append((e, f"already mailed: on {recently_sent[e][:10]}"))
        else:
            send.append(e)
    return Checked(send=send, skipped=skipped, found=len(emails))
