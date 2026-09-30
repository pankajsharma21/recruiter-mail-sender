"""The encrypted paths real providers use (STARTTLS on 587 like Gmail, SSL on 465 like
Yahoo/Zoho), and a mail as real as it gets: Hindi text, a rupee sign, a long subject,
a 50 KB PDF."""

import ssl
import subprocess

import pytest

from conftest import smtp_account
from fakesmtp import FakeSMTP
from mailsender import campaign, transport
from mailsender.accounts import Account
from mailsender.message import Template, build


@pytest.fixture(scope="session")
def cert(tmp_path_factory):
    d = tmp_path_factory.mktemp("tls")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-keyout", str(d / "key.pem"), "-out", str(d / "cert.pem"), "-subj", "/CN=localhost",
                    "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1"],
                   check=True, capture_output=True)
    return str(d / "cert.pem"), str(d / "key.pem")


@pytest.fixture
def trust(cert, monkeypatch):
    """The client trusts the test certificate, and only it: verification stays on."""
    real = ssl.create_default_context
    monkeypatch.setattr(transport.ssl, "create_default_context", lambda *a, **k: real(cafile=cert[0]))


def tls_server(cert, mode):
    return FakeSMTP(tls=mode, certfile=cert[0], keyfile=cert[1])


@pytest.mark.parametrize("mode", ["starttls", "ssl"])
def test_encrypted_send(cert, trust, fast, history, template, mode):
    s = tls_server(cert, mode)
    try:
        a = smtp_account(s, security=mode, host="localhost")
        r = campaign.run(a, template, ["test.recipient@outlook.com"], history, gap=0)
    finally:
        s.close()
    assert r.sent == ["test.recipient@outlook.com"] and not r.failed
    assert s.tls_used and s.tls_used[0].startswith("TLSv1.")
    assert s.messages[0]["To"] == "test.recipient@outlook.com"


def test_a_fake_certificate_is_refused(cert, fast, history, template):
    s = tls_server(cert, "starttls")       # no `trust`: the normal system trust store
    try:
        r = campaign.run(smtp_account(s, security="starttls", host="localhost"), template,
                         ["a@x.com"], history, gap=0)
    finally:
        s.close()
    assert r.sent == [] and "cannot be trusted" in r.stopped and s.logins == 0   # password never sent


def test_starttls_asked_but_the_server_has_none(smtp, fast, history, template):
    r = campaign.run(smtp_account(smtp, security="starttls"), template, ["a@x.com"], history, gap=0)
    assert r.sent == [] and "check the security setting" in r.stopped


def test_no_encryption_but_the_server_wants_it(cert, fast, history, template):
    s = tls_server(cert, "starttls")
    try:
        r = campaign.run(smtp_account(s, security="none"), template, ["a@x.com"], history, gap=0)
    finally:
        s.close()
    assert r.sent == [] and "set Security to STARTTLS" in r.stopped and s.logins == 0


def test_a_real_looking_mail_arrives_intact(cert, trust, tmp_path, history):
    pdf = tmp_path / "Resume.pdf"
    body = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\n" + bytes(range(256)) * 200 + b"\n%%EOF\n"
    pdf.write_bytes(body)                                   # ~51 KB, every byte value included
    t = Template(id="t", name="t",
                 subject="Application: Senior Java Developer (5 yrs, Spring Boot, Microservices) - {company} - ₹ CTC discussable",
                 body="नमस्ते,\n\nमैं {company} में Java Developer की position के लिए apply करना चाहता हूँ.\n"
                      "Expected CTC: ₹18 LPA. " + "Long line " * 60 + "\n\nRegards,\nPriya",
                 attachments=[str(pdf)])
    s = tls_server(cert, "starttls")
    try:
        a = Account(id="g", kind="smtp", email="test.sender@gmail.com", name="Priya Sharma", host="localhost",
                    port=s.port, security="starttls", username="me@test.example", password="secret")
        tx = transport.open_transport(a)
        tx.send(build(a, t, "test.recipient@outlook.com"))
        tx.close()
    finally:
        s.close()
    raw, m = s.raw[0], s.messages[0]
    raw.decode("ascii")                                      # all 7-bit on the wire: safe for any server
    assert max(len(l) for l in raw.splitlines()) <= 998      # the SMTP line-length limit
    assert m["From"] == "Priya Sharma <test.sender@gmail.com>" and m["To"] == "test.recipient@outlook.com"
    assert m["Subject"].endswith("- your company - ₹ CTC discussable")   # outlook.com is free mail
    assert m["Date"] and m["Message-ID"].endswith("@gmail.com>")
    text = m.get_body(("plain",)).get_content()
    assert "नमस्ते" in text and "₹18 LPA" in text and "मैं your company में" in text
    (att,) = list(m.iter_attachments())
    assert att.get_content_type() == "application/pdf" and att.get_filename() == "Resume.pdf"
    assert att.get_content() == body                         # byte for byte
