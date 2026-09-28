"""The mail accounts a person sends from: an app password (SMTP) or a Gmail token.

Secrets never live in the repo. They are kept in one JSON file under the user's
config directory, readable only by the user (mode 600), next to any token files.
"""

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("MAILSENDER_HOME") or Path.home() / ".config/recruiter-mail-sender")

# Mail server per provider, picked from the address. Anything else: fill in by hand.
PROVIDERS = {
    "gmail":   {"label": "Gmail",           "host": "smtp.gmail.com",        "port": 587, "security": "starttls",
                "domains": ["gmail.com", "googlemail.com"]},
    "outlook": {"label": "Outlook/Hotmail", "host": "smtp-mail.outlook.com", "port": 587, "security": "starttls",
                "domains": ["outlook.com", "hotmail.com", "live.com", "msn.com"]},
    "yahoo":   {"label": "Yahoo",           "host": "smtp.mail.yahoo.com",   "port": 465, "security": "ssl",
                "domains": ["yahoo.com", "yahoo.in", "yahoo.co.in", "ymail.com"]},
    "zoho":    {"label": "Zoho",            "host": "smtp.zoho.com",         "port": 465, "security": "ssl",
                "domains": ["zoho.com", "zohomail.com"]},
    "zoho_in": {"label": "Zoho (India)",    "host": "smtp.zoho.in",          "port": 465, "security": "ssl",
                "domains": ["zohomail.in"]},
}
SECURITY = ("starttls", "ssl", "none")
KINDS = ("smtp", "gmail_token")


def guess_provider(email: str) -> str | None:
    domain = email.rsplit("@", 1)[-1].lower()
    for key, p in PROVIDERS.items():
        if domain in p["domains"]:
            return key
    return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "account"


@dataclass
class Account:
    id: str
    kind: str                      # "smtp" (app password) or "gmail_token"
    email: str
    name: str = ""                 # shown as the sender name: "Priya Sharma <priya@...>"
    # SMTP / app password
    host: str = ""
    port: int = 587
    security: str = "starttls"
    username: str = ""             # usually the address itself
    password: str = ""
    # Gmail API
    token_path: str = ""
    daily_limit: int = 400         # free Gmail allows about 500 a day; stay under it
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"unknown account type {self.kind!r}")
        self.email = self.email.strip()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", self.email):
            raise ValueError(f"not an email address: {self.email!r}")
        self.port = int(self.port)
        self.daily_limit = max(1, int(self.daily_limit))
        if self.kind == "smtp":
            p = PROVIDERS.get(guess_provider(self.email) or "", {})
            if not self.host.strip() and p:
                # The whole server comes from the provider: Yahoo and Zoho use 465/SSL, not 587.
                self.host, self.port, self.security = p["host"], p["port"], p["security"]
            self.host = self.host.strip()
            if not self.host:
                raise ValueError("no mail server known for this address: fill in the SMTP server")
            if self.security not in SECURITY:
                raise ValueError(f"security must be one of {SECURITY}")
            self.username = self.username.strip() or self.email
            # Google shows app passwords as "abcd efgh ijkl mnop"; the spaces are not part of it.
            self.password = re.sub(r"\s+", "", self.password) if guess_provider(self.email) == "gmail" else self.password
            if not self.password:
                raise ValueError("the app password is empty")
        else:
            if not self.token_path:
                raise ValueError("choose the token.json file")

    @property
    def sender(self) -> str:
        from email.utils import formataddr
        return formataddr((self.name, self.email)) if self.name else self.email

    def public(self) -> dict:
        """What the screen may see: everything but the secret."""
        d = asdict(self)
        d["password"] = "••••••••" if self.password else ""
        return d


class Accounts:
    """accounts.json in the config directory, written with owner-only permissions."""

    def __init__(self, config_dir: Path = CONFIG_DIR):
        self.dir = config_dir
        self.path = config_dir / "accounts.json"

    def all(self) -> list[Account]:
        if not self.path.exists():
            return []
        out = []
        for d in json.loads(self.path.read_text()):
            try:
                out.append(Account(**{k: v for k, v in d.items() if k in Account.__dataclass_fields__}))
            except (TypeError, ValueError):
                continue  # a hand-edited entry with a typo should not hide the others
        return out

    def get(self, account_id: str) -> Account:
        for a in self.all():
            if a.id == account_id or a.email.lower() == account_id.lower():
                return a
        known = ", ".join(a.id for a in self.all()) or "none"
        raise KeyError(f"no account {account_id!r} (known: {known})")

    def new_id(self, email: str) -> str:
        taken = {a.id for a in self.all()}
        base = slug(email.split("@")[0])
        pid, n = base, 2
        while pid in taken:
            pid, n = f"{base}-{n}", n + 1
        return pid

    def save(self, account: Account) -> None:
        keep = [a for a in self.all() if a.id != account.id]
        self._write(keep + [account])

    def delete(self, account_id: str) -> None:
        self._write([a for a in self.all() if a.id != account_id])

    def _write(self, accounts: list[Account]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        tmp = self.path.with_suffix(".tmp")
        # Create it private from the first byte, then swap it in: no moment where
        # the password sits in a world-readable file.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump([asdict(a) for a in accounts], f, indent=2)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
