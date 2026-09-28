"""Create a Gmail token.json from a Google "Desktop app" client_secret.json.

The standard installed-app flow: open Google's consent page, catch the answer on a
one-shot local port, trade the code for a refresh token. PKCE and a random state
guard the exchange. Only the gmail.send scope is asked for, so the token can send
mail but never read it.
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from .transport import GMAIL_SEND_SCOPE

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class AuthError(Exception):
    pass


def read_client(client_secret: Path) -> dict:
    try:
        d = json.loads(Path(client_secret).expanduser().read_text())
    except (OSError, ValueError) as e:
        raise AuthError(f"cannot read {client_secret}: {e}") from None
    c = d.get("installed") or d.get("web")
    if not c or not c.get("client_id") or not c.get("client_secret"):
        raise AuthError("this is not a Google OAuth client file (expected an \"installed\" Desktop app client)")
    if "web" in d:
        raise AuthError("this is a Web client; create a \"Desktop app\" OAuth client instead")
    return c


def authorize(client_secret: Path, token_out: Path, open_browser=webbrowser.open, timeout: int = 300) -> str:
    """Run the consent flow and write token.json. Returns the file's path."""
    client = read_client(client_secret)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    got: dict = {}
    done = threading.Event()

    class Catch(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" not in q and "error" not in q:
                self.send_response(404); self.end_headers(); return
            if q.get("state", [""])[0] != state:
                got["error"] = "state mismatch (the answer did not come from this sign-in)"
            elif "error" in q:
                got["error"] = q["error"][0]
            else:
                got["code"] = q["code"][0]
            ok = "code" in got
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(("<h2>" + ("Done. You can close this tab and go back to the mail sender."
                                        if ok else "Sign-in did not complete: " + got.get("error", ""))
                              + "</h2>").encode())
            done.set()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Catch)
    redirect = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = AUTH_URI + "?" + urllib.parse.urlencode({
            "client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
            "scope": GMAIL_SEND_SCOPE, "access_type": "offline", "prompt": "consent",
            "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        })
        open_browser(url)
        if not done.wait(timeout):
            raise AuthError("no answer from Google within 5 minutes; try again")
    finally:
        server.shutdown()
        server.server_close()
    if "error" in got:
        raise AuthError(f"Google sign-in failed: {got['error']}")

    body = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": got["code"], "redirect_uri": redirect,
        "client_id": client["client_id"], "client_secret": client["client_secret"], "code_verifier": verifier,
    }).encode()
    token_uri = client.get("token_uri") or TOKEN_URI
    try:
        with urllib.request.urlopen(urllib.request.Request(token_uri, data=body), timeout=60) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AuthError(f"Google refused the code: {e.code} {e.read()[:200]!r}") from None
    except OSError as e:
        raise AuthError(f"cannot reach Google: {e}") from None
    if not tok.get("refresh_token"):
        raise AuthError("Google returned no refresh token; remove the app's access at "
                        "myaccount.google.com/permissions and try again")

    token_out = Path(token_out).expanduser()
    token_out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(token_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        # Same layout google-auth writes, so the file also works with other tools.
        json.dump({"token": tok["access_token"], "refresh_token": tok["refresh_token"],
                   "token_uri": token_uri, "client_id": client["client_id"],
                   "client_secret": client["client_secret"], "scopes": [GMAIL_SEND_SCOPE]}, f, indent=2)
    os.chmod(token_out, 0o600)
    return str(token_out)
