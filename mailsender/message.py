"""Templates (subject, body, attachments) and turning one into a real email.

A template is one JSON file in templates/, so a person can keep several ("Java
roles", "Accounts roles") and pick one on the screen.
"""

import json
import mimetypes
import re
from dataclasses import asdict, dataclass, field
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from .accounts import Account, slug

FREE_MAIL = {"gmail.com", "googlemail.com", "yahoo.com", "yahoo.in", "yahoo.co.in", "ymail.com",
             "outlook.com", "hotmail.com", "live.com", "msn.com", "rediffmail.com", "icloud.com",
             "aol.com", "proton.me", "protonmail.com", "zoho.com", "zohomail.in"}
PLACEHOLDERS = ("{email}", "{company}")
MAX_ATTACHMENTS_MB = 20   # Gmail's limit is 25 MB after encoding grows the file by a third


@dataclass
class Template:
    id: str
    name: str
    subject: str
    body: str
    attachments: list[str] = field(default_factory=list)   # file paths

    def __post_init__(self):
        if not self.subject.strip():
            raise ValueError("the subject is empty")
        if not self.body.strip():
            raise ValueError("the message is empty")
        total = 0
        for p in self.attachments:
            f = Path(p).expanduser()
            if not f.is_file():
                raise ValueError(f"attachment not found: {p}")
            total += f.stat().st_size
        if total > MAX_ATTACHMENTS_MB * 1024 * 1024:
            raise ValueError(f"attachments are over {MAX_ATTACHMENTS_MB} MB; mail servers will refuse them")


def company_of(email: str) -> str:
    """"hr@acme-tech.co.in" -> "Acme Tech"; a Gmail/Yahoo address has no company."""
    domain = email.rsplit("@", 1)[-1].lower()
    if domain in FREE_MAIL:
        return "your company"
    label = domain.split(".")[0]
    return " ".join(w.capitalize() for w in re.split(r"[-_]+", label) if w) or "your company"


def fill(text: str, to: str) -> str:
    # Plain replace, not str.format: a "{" anywhere else in the message must not crash it.
    return text.replace("{email}", to).replace("{company}", company_of(to))


def build(account: Account, template: Template, to: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = account.sender
    msg["To"] = to
    msg["Subject"] = fill(template.subject, to).strip()
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=account.email.rsplit("@", 1)[-1])
    msg.set_content(fill(template.body, to))
    for p in template.attachments:
        f = Path(p).expanduser()
        ctype, _ = mimetypes.guess_type(f.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(f.read_bytes(), maintype=maintype, subtype=subtype, filename=f.name)
    return msg


class Templates:
    def __init__(self, folder: Path):
        self.dir = folder

    def all(self) -> list[Template]:
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                d = json.loads(f.read_text())
                out.append(Template(**{k: v for k, v in d.items() if k in Template.__dataclass_fields__}))
            except (OSError, TypeError, ValueError):
                continue  # a broken file (or a moved attachment) should not hide the others
        return out

    def load_raw(self) -> list[dict]:
        """Every template as saved, even one whose attachment went missing, for the screen."""
        out = []
        for f in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(f.read_text()))
            except (OSError, ValueError):
                continue
        return out

    def get(self, template_id: str) -> Template:
        f = self.dir / f"{slug(template_id)}.json"
        if not f.exists():
            known = ", ".join(t.id for t in self.all()) or "none"
            raise KeyError(f"no template {template_id!r} (known: {known})")
        d = json.loads(f.read_text())
        return Template(**{k: v for k, v in d.items() if k in Template.__dataclass_fields__})

    def new_id(self, name: str) -> str:
        base = slug(name)
        tid, n = base, 2
        while (self.dir / f"{tid}.json").exists():
            tid, n = f"{base}-{n}", n + 1
        return tid

    def save(self, t: Template) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / f"{t.id}.json").write_text(json.dumps(asdict(t), indent=2, ensure_ascii=False))
