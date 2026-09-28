import json
import os
import stat

import pytest

from mailsender import recipients
from mailsender.accounts import Account, Accounts
from mailsender.message import Template, company_of, fill


# --- accounts ---------------------------------------------------------------

def test_gmail_is_filled_in_and_app_password_spaces_dropped():
    a = Account(id="p", kind="smtp", email="Priya@Gmail.com ", password="abcd efgh ijkl mnop")
    assert (a.host, a.port, a.security) == ("smtp.gmail.com", 587, "starttls")
    assert a.password == "abcdefghijklmnop" and a.username == "Priya@Gmail.com"


def test_unknown_domain_needs_a_server():
    with pytest.raises(ValueError, match="SMTP server"):
        Account(id="x", kind="smtp", email="me@company.in", password="p")
    a = Account(id="x", kind="smtp", email="me@company.in", password="p w", host="mail.company.in", port=465,
                security="ssl")
    assert a.password == "p w"   # only Gmail's display spaces are removed


def test_accounts_file_is_private_and_the_screen_never_sees_the_password(tmp_path):
    store = Accounts(tmp_path / "cfg")
    store.save(Account(id="p", kind="smtp", email="p@gmail.com", password="abcdabcdabcdabcd"))
    mode = stat.S_IMODE(os.stat(store.path).st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(os.stat(store.dir).st_mode) == 0o700
    assert store.get("p").password == "abcdabcdabcdabcd"
    assert "abcd" not in json.dumps(store.get("p").public())


def test_a_broken_entry_does_not_hide_the_others(tmp_path):
    store = Accounts(tmp_path)
    store.save(Account(id="ok", kind="smtp", email="ok@gmail.com", password="x"))
    data = json.loads(store.path.read_text()) + [{"id": "bad", "kind": "carrier-pigeon", "email": "x"}]
    store.path.write_text(json.dumps(data))
    assert [a.id for a in store.all()] == ["ok"]


# --- recipients -------------------------------------------------------------

def test_check_explains_every_skip():
    text = """HR: hr@acme.com, HR@ACME.COM
    careers [at] beta [dot] in ; noreply@jobs.com ; priya@gamil.com
    logo@2x.png  sales@gamma.co"""
    c = recipients.check(text, recently_sent={"sales@gamma.co": "2026-09-20T10:00:00"}, blocked={"spam.com"})
    assert c.send == ["hr@acme.com", "careers@beta.in"]
    why = dict(c.skipped)
    assert why["hr@acme.com"] == "duplicate in the list"
    assert why["noreply@jobs.com"].startswith("no-reply")
    assert "gmail.com" in why["priya@gamil.com"]
    assert why["sales@gamma.co"] == "already mailed: on 2026-09-20"
    assert c.found == 6 and c.summary()["skipped"] == {"duplicate in the list": 1, "no-reply address": 1,
                                                       "looks like a typo": 1, "already mailed": 1}


# --- message ----------------------------------------------------------------

def test_company_from_the_address():
    assert company_of("hr@acme-tech.co.in") == "Acme Tech"
    assert company_of("x@yahoo.co.in") == "your company"
    assert fill("{company} / {email} / {x}", "a@b-c.com") == "B C / a@b-c.com / {x}"


def test_template_checks(tmp_path):
    with pytest.raises(ValueError, match="subject"):
        Template(id="t", name="t", subject=" ", body="b")
    with pytest.raises(ValueError, match="attachment not found"):
        Template(id="t", name="t", subject="s", body="b", attachments=[str(tmp_path / "gone.pdf")])


def test_provider_fills_port_and_security_too():
    for email, want in [("a@yahoo.co.in", ("smtp.mail.yahoo.com", 465, "ssl")),
                        ("a@zoho.com", ("smtp.zoho.com", 465, "ssl")),
                        ("a@zohomail.in", ("smtp.zoho.in", 465, "ssl")),
                        ("a@outlook.com", ("smtp-mail.outlook.com", 587, "starttls"))]:
        a = Account(id="x", kind="smtp", email=email, password="p")
        assert (a.host, a.port, a.security) == want
    # A server typed by hand is kept as typed.
    a = Account(id="x", kind="smtp", email="a@yahoo.com", password="p", host="relay.me", port=2525, security="none")
    assert (a.host, a.port, a.security) == ("relay.me", 2525, "none")
