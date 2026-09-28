"""SQLite log of every mail sent, so nobody is mailed twice within the cooldown and
the daily limit counts what really went out."""

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sent (
    id          INTEGER PRIMARY KEY,
    email       TEXT NOT NULL,
    account     TEXT NOT NULL,     -- the sender address
    template    TEXT NOT NULL,
    subject     TEXT NOT NULL,
    status      TEXT NOT NULL,     -- 'sent', 'failed' (try again later) or 'rejected' (dead address)
    error       TEXT,
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sent_email ON sent(email, at);
CREATE INDEX IF NOT EXISTS sent_account ON sent(account, at);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class History:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        # The screen reads while the sending thread writes.
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)

    def log(self, email: str, account: str, template: str, subject: str, status: str, error: str = "") -> None:
        with self.lock:
            self.db.execute("INSERT INTO sent (email, account, template, subject, status, error, at) "
                            "VALUES (?,?,?,?,?,?,?)", (email, account, template, subject, status, error, _now()))
            self.db.commit()

    def recently_sent(self, days: float) -> dict[str, str]:
        """address -> last time it was mailed successfully, within the last `days`."""
        if days <= 0:
            return {}
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self.lock:
            rows = self.db.execute("SELECT email, MAX(at) FROM sent WHERE status='sent' AND at>=? GROUP BY email",
                                   (cutoff,)).fetchall()
        return dict(rows)

    def rejected(self) -> dict[str, str]:
        """address -> why the server refused it for good (e.g. "550 no such user").
        A later successful send clears it, in case the address came back to life."""
        with self.lock:
            rows = self.db.execute(
                "SELECT email, error FROM sent s WHERE status='rejected' AND NOT EXISTS "
                "(SELECT 1 FROM sent t WHERE t.email=s.email AND t.status='sent' AND t.at>s.at)").fetchall()
        return dict(rows)

    def sent_today(self, account: str) -> int:
        """Mails this account sent in the last 24 hours (the window Gmail counts)."""
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat(timespec="seconds")
        with self.lock:
            (n,) = self.db.execute("SELECT COUNT(*) FROM sent WHERE account=? AND status='sent' AND at>=?",
                                   (account.lower(), cutoff)).fetchone()
        return n

    def recent(self, limit: int = 200) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT email, account, template, status, error, at FROM sent "
                                   "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("email", "account", "template", "status", "error", "at"), r)) for r in rows]
