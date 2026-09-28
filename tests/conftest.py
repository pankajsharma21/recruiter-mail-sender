import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from fakesmtp import FakeSMTP  # noqa: E402
from mailsender import campaign  # noqa: E402
from mailsender.accounts import Account  # noqa: E402
from mailsender.history import History  # noqa: E402
from mailsender.message import Template  # noqa: E402


@pytest.fixture
def smtp():
    s = FakeSMTP()
    yield s
    s.close()


@pytest.fixture
def fast(monkeypatch):
    """No real waiting between mails or before retries."""
    monkeypatch.setattr(campaign, "RETRY_WAITS", (0.01, 0.01))
    monkeypatch.setattr(campaign, "MIN_GAP", 0)


def smtp_account(server, **kw) -> Account:
    return Account(**{"id": "me", "kind": "smtp", "email": "me@test.example", "name": "Test Me",
                      "host": "127.0.0.1", "port": server.port, "security": "none",
                      "password": "secret", **kw})


@pytest.fixture
def history(tmp_path):
    return History(tmp_path / "sent.db")


@pytest.fixture
def template(tmp_path):
    cv = tmp_path / "Resume.pdf"
    cv.write_bytes(b"%PDF-1.4 fake resume")
    return Template(id="java", name="Java", subject="Application for {company}",
                    body="Hi,\n\nI would like to work at {company}. {not a placeholder}\n", attachments=[str(cv)])
