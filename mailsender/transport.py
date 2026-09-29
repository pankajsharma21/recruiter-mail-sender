"""Send one EmailMessage through an account: SMTP with an app password, or the Gmail API.

Both raise SendError. `temporary=True` means trying again later may work (network
down, server busy); False means it will not (wrong password, address rejected).
`account=True` means the problem is the account itself (login refused, token revoked,
daily limit hit), so every other address would fail the same way.
"""

import base64
import json
import os
import smtplib
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage
from pathlib import Path

from .accounts import Account

GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
TIMEOUT = 60


class SendError(Exception):
    def __init__(self, message: str, temporary: bool, account: bool = False):
        super().__init__(message)
        self.temporary = temporary
        self.account = account


_LIMIT_WORDS = ("sending limit", "daily user sending", "quota exceeded", "too many messages", "rate limit")


def _is_limit(text: str) -> bool:
    return any(w in text.lower() for w in _LIMIT_WORDS)


def open_transport(account: Account):
    return SmtpTransport(account) if account.kind == "smtp" else GmailApiTransport(account)


class SmtpTransport:
    """One connection reused for many mails, reopened if the server drops it."""

    def __init__(self, account: Account):
        self.a = account
        self.conn: smtplib.SMTP | None = None

    def login(self) -> None:
        a = self.a
        try:
            if a.security == "ssl":
                conn = smtplib.SMTP_SSL(a.host, a.port, timeout=TIMEOUT, context=ssl.create_default_context())
            else:
                conn = smtplib.SMTP(a.host, a.port, timeout=TIMEOUT)
                conn.ehlo()
                if a.security == "starttls":
                    conn.starttls(context=ssl.create_default_context())
                    conn.ehlo()
            if a.security == "none" and conn.has_extn("starttls") and not conn.has_extn("auth"):
                conn.close()
                raise SendError(f"{a.host} wants an encrypted connection: set Security to STARTTLS (port 587)",
                                temporary=False, account=True)
            conn.login(a.username, a.password)
        except smtplib.SMTPAuthenticationError as e:
            raise SendError(_auth_hint(a, e), temporary=False, account=True) from None
        except ssl.SSLCertVerificationError as e:
            # Never fall back to an unchecked connection: that would hand the password to
            # whoever is pretending to be the mail server.
            raise SendError(f"{a.host} sent a certificate that cannot be trusted ({e.verify_message}); "
                            "check the server name", temporary=False, account=True) from None
        except smtplib.SMTPNotSupportedError as e:
            raise SendError(f"the server does not support this login ({e}); check the security setting",
                            temporary=False, account=True) from None
        except (OSError, smtplib.SMTPException) as e:
            raise SendError(f"cannot reach {a.host}:{a.port} ({_short(e)})", temporary=True) from None
        self.conn = conn

    def send(self, msg: EmailMessage) -> str:
        for attempt in (1, 2):
            if self.conn is None:
                self.login()
            try:
                refused = self.conn.send_message(msg)
            except smtplib.SMTPServerDisconnected:
                self.conn = None  # idle too long; one silent reconnect
                if attempt == 2:
                    raise SendError("the server closed the connection", temporary=True) from None
                continue
            except smtplib.SMTPRecipientsRefused as e:
                code, text = next(iter(e.recipients.values()))
                raise SendError(f"address refused: {code} {_text(text)}", temporary=code < 500) from None
            except smtplib.SMTPResponseException as e:
                self.close()
                text = _text(e.smtp_error)
                if _is_limit(text):
                    raise SendError(f"sending limit reached: {e.smtp_code} {text}", temporary=False,
                                    account=True) from None
                raise SendError(f"server said {e.smtp_code} {text}", temporary=e.smtp_code < 500) from None
            except (OSError, smtplib.SMTPException) as e:
                self.close()
                raise SendError(f"sending failed ({_short(e)})", temporary=True) from None
            if refused:
                code, text = next(iter(refused.values()))
                raise SendError(f"address refused: {code} {_text(text)}", temporary=code < 500)
            return ""
        raise AssertionError("unreachable")

    def close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.quit()
            except Exception:
                pass
            self.conn = None


class GmailApiTransport:
    """Gmail REST API with an OAuth token.json (the file google-auth writes).

    SMTP refuses the narrow gmail.send scope, so the token is used over HTTPS.
    The short-lived access token is refreshed from the refresh token as needed
    and written back, so the file stays valid.
    """

    def __init__(self, account: Account):
        self.a = account
        self.path = Path(account.token_path).expanduser()
        self.info: dict = {}

    def login(self) -> None:
        try:
            self.info = json.loads(self.path.read_text())
        except FileNotFoundError:
            raise SendError(f"token file not found: {self.path}", temporary=False, account=True) from None
        except (OSError, ValueError) as e:
            raise SendError(f"cannot read the token file ({e})", temporary=False, account=True) from None
        missing = [k for k in ("refresh_token", "client_id", "client_secret") if not self.info.get(k)]
        if missing:
            raise SendError(f"token file has no {', '.join(missing)}; create it again", temporary=False,
                            account=True)
        scopes = self.info.get("scopes") or []
        if isinstance(scopes, str):
            scopes = scopes.split()
        if scopes and not any(s in (GMAIL_SEND_SCOPE, "https://mail.google.com/") for s in scopes):
            raise SendError("this token is not allowed to send mail (no gmail.send scope)", temporary=False,
                            account=True)
        self._refresh()

    def _refresh(self) -> None:
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token", "refresh_token": self.info["refresh_token"],
            "client_id": self.info["client_id"], "client_secret": self.info["client_secret"],
        }).encode()
        url = self.info.get("token_uri") or "https://oauth2.googleapis.com/token"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=TIMEOUT) as r:
                got = json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = _http_detail(e)
            if "invalid_grant" in detail:
                raise SendError("Google no longer accepts this token (expired or revoked); create a new one",
                                temporary=False, account=True) from None
            raise SendError(f"token refresh failed: {e.code} {detail}", temporary=e.code >= 500,
                            account=e.code < 500) from None
        except (OSError, ValueError) as e:
            raise SendError(f"cannot reach Google ({_short(e)})", temporary=True) from None
        self.info["token"] = got["access_token"]
        self.info["expires_at"] = time.time() + int(got.get("expires_in", 3600)) - 120
        self._save()

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(self.info, f, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            pass  # a read-only token file still works for this run

    def send(self, msg: EmailMessage) -> str:
        if not self.info:
            self.login()
        elif time.time() > self.info.get("expires_at", 0):
            self._refresh()
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        req = urllib.request.Request(
            GMAIL_SEND_URL, data=json.dumps({"raw": raw}).encode(), method="POST",
            headers={"Authorization": f"Bearer {self.info['token']}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read()).get("id", "")
        except urllib.error.HTTPError as e:
            detail = _http_detail(e)
            if e.code == 401:
                self.info["expires_at"] = 0  # refresh on the retry
                raise SendError(f"Google rejected the access token ({detail})", temporary=True) from None
            if "daily" in detail.lower() and _is_limit(detail):
                raise SendError(f"Gmail daily sending limit reached ({detail})", temporary=False,
                                account=True) from None
            if e.code == 429 or "rateLimitExceeded" in detail:
                raise SendError(f"Gmail says slow down ({detail})", temporary=True) from None
            if e.code == 403:
                raise SendError(f"Gmail refused this account ({detail})", temporary=False, account=True) from None
            if e.code == 400 and "Invalid To header" in detail:
                raise SendError("Gmail rejected the address", temporary=False) from None
            raise SendError(f"Gmail said {e.code} {detail}", temporary=e.code >= 500) from None
        except (OSError, ValueError) as e:
            raise SendError(f"cannot reach Gmail ({_short(e)})", temporary=True) from None

    def close(self) -> None:
        pass


def _auth_hint(a: Account, e: smtplib.SMTPAuthenticationError) -> str:
    text = _text(e.smtp_error)
    if "smtp.gmail.com" in a.host:
        return ("Gmail refused the login. Use a 16-letter app password (myaccount.google.com/apppasswords, "
                f"needs 2-Step Verification), not your normal password. Server said: {e.smtp_code} {text}")
    return f"login refused: {e.smtp_code} {text}. Check the address and app password."


def _http_detail(e: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(e.read())
        err = body.get("error")
        if isinstance(err, dict):
            return err.get("message") or json.dumps(err)[:200]
        return str(body.get("error_description") or err or body)[:200]
    except Exception:
        return e.reason or ""


def _text(t) -> str:
    t = t.decode(errors="replace") if isinstance(t, bytes) else str(t)
    return " ".join(t.split())[:200]


def _short(e: Exception) -> str:
    if isinstance(e, socket.gaierror):
        return "no internet, or the server name is wrong"
    if isinstance(e, (socket.timeout, TimeoutError)):
        return "timed out"
    return (str(e) or type(e).__name__)[:160]
