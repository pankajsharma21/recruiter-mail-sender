import base64
import http.client
import io
import json
import threading
import urllib.error
from email import message_from_bytes, policy

import pytest

from conftest import smtp_account
from mailsender import transport, ui
from mailsender.accounts import Account
from mailsender.message import build


# --- Gmail API, with Google faked -------------------------------------------

class FakeGoogle:
    def __init__(self, refresh_error=None, send_error=None):
        self.refresh_error, self.send_error = refresh_error, send_error
        self.refreshes, self.sent = 0, []

    def __call__(self, req, timeout=None):
        url = req.full_url
        if "oauth2" in url:
            if self.refresh_error:
                raise urllib.error.HTTPError(url, 400, "bad", {}, io.BytesIO(json.dumps(self.refresh_error).encode()))
            self.refreshes += 1
            return io.BytesIO(json.dumps({"access_token": f"at-{self.refreshes}", "expires_in": 3599}).encode())
        assert req.get_header("Authorization") == f"Bearer at-{self.refreshes}"
        if self.send_error:
            code, body = self.send_error
            raise urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(json.dumps(body).encode()))
        raw = json.loads(req.data)["raw"]
        self.sent.append(message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default))
        return io.BytesIO(b'{"id": "msg-1"}')


def token_account(tmp_path, **token):
    f = tmp_path / "token.json"
    f.write_text(json.dumps({"token": "old", "refresh_token": "rt", "client_id": "cid", "client_secret": "cs",
                             "token_uri": "https://oauth2.googleapis.com/token",
                             "scopes": ["https://www.googleapis.com/auth/gmail.send"], **token}))
    return Account(id="g", kind="gmail_token", email="me@gmail.com", token_path=str(f)), f


def test_gmail_token_refreshes_sends_and_keeps_the_file_valid(tmp_path, monkeypatch, template):
    g = FakeGoogle()
    monkeypatch.setattr(transport.urllib.request, "urlopen", g)
    a, f = token_account(tmp_path)
    tx = transport.open_transport(a)
    assert tx.send(build(a, template, "hr@acme.com")) == "msg-1"
    assert tx.send(build(a, template, "hr@beta.com")) == "msg-1"
    assert g.refreshes == 1                                    # one refresh for the whole list
    assert [m["To"] for m in g.sent] == ["hr@acme.com", "hr@beta.com"]
    assert json.loads(f.read_text())["token"] == "at-1"        # the new access token was saved
    assert json.loads(f.read_text())["refresh_token"] == "rt"


def test_revoked_token_is_an_account_problem(tmp_path, monkeypatch):
    monkeypatch.setattr(transport.urllib.request, "urlopen", FakeGoogle(refresh_error={"error": "invalid_grant"}))
    a, _ = token_account(tmp_path)
    with pytest.raises(transport.SendError) as e:
        transport.open_transport(a).login()
    assert e.value.account and not e.value.temporary and "create a new one" in str(e.value)


def test_a_read_only_token_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(transport.urllib.request, "urlopen", FakeGoogle())
    a, _ = token_account(tmp_path, scopes=["https://www.googleapis.com/auth/gmail.readonly"])
    with pytest.raises(transport.SendError, match="not allowed to send"):
        transport.open_transport(a).login()


def test_gmail_daily_limit_is_an_account_problem(tmp_path, monkeypatch, template):
    body = {"error": {"code": 429, "message": "Daily user sending limit exceeded."}}
    monkeypatch.setattr(transport.urllib.request, "urlopen", FakeGoogle(send_error=(429, body)))
    a, _ = token_account(tmp_path)
    with pytest.raises(transport.SendError) as e:
        transport.open_transport(a).send(build(a, template, "x@y.com"))
    assert e.value.account


# --- the screen's server ----------------------------------------------------

@pytest.fixture
def server(tmp_path, smtp):
    app = ui.App(tmp_path / "project", tmp_path / "cfg")
    holder = {"port": 0}
    srv = ui.http.server.ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(app, "KEY", holder))
    holder["port"] = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield app, holder["port"]
    srv.shutdown(); srv.server_close()


def call(port, path, body=b"{}", key="KEY", host=None, extra=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"X-Key": key, "Host": host or f"127.0.0.1:{port}", **(extra or {})}
    c.request("POST", path, body=body, headers=headers)
    r = c.getresponse()
    return r.status, r.read()


def test_api_needs_the_page_key_and_a_local_host(server):
    _, port = server
    assert call(port, "/api/state")[0] == 200
    assert call(port, "/api/state", key="")[0] == 403                      # another website has no key
    assert call(port, "/api/state", host=f"evil.example:{port}")[0] == 403  # DNS rebinding
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", "/", headers={"Host": "evil.example"})
    assert c.getresponse().status == 403


def test_saving_again_keeps_the_hidden_password(server, smtp):
    app, port = server
    a = smtp_account(smtp)
    code, out = call(port, "/api/account/save", json.dumps({**a.__dict__, "id": None}).encode())
    new_id = json.loads(out)["id"]
    shown = [x for x in json.loads(out)["accounts"] if x["id"] == new_id][0]
    assert shown["password"] == ui.MASK                                    # the screen gets a mask
    call(port, "/api/account/save", json.dumps({**shown, "name": "Renamed"}).encode())
    assert app.accounts.get(new_id).password == "secret" and app.accounts.get(new_id).name == "Renamed"
    code, out = call(port, "/api/account/test", json.dumps({"id": new_id}).encode())
    assert json.loads(out)["ok"] is True


def test_token_uploads_go_to_the_private_folder(server):
    app, port = server
    code, out = call(port, "/api/upload", json.dumps({"refresh_token": "rt"}).encode(),
                     extra={"X-Filename": "token.json", "X-Kind": "token"})
    path = json.loads(out)["path"]
    assert path.startswith(str(app.tokens)) and not path.startswith(str(app.root))
    code, out = call(port, "/api/upload", b"{}", extra={"X-Filename": "x.json", "X-Kind": "token"})
    assert code == 400 and b"not a Gmail token" in out


def test_full_send_through_the_screen(server, smtp, template, monkeypatch):
    from mailsender import campaign
    monkeypatch.setattr(campaign, "MIN_GAP", 0)
    app, port = server
    a = smtp_account(smtp); app.accounts.save(a)
    app.templates.save(template)
    text = json.dumps({"text": "hr@acme.com hr@acme.com noreply@x.com", "cooldown_days": 30}).encode()
    code, out = call(port, "/api/check", text)
    assert json.loads(out)["send"] == ["hr@acme.com"]
    body = json.dumps({"account": "me", "template": "java", "text": "hr@acme.com", "gap": 0}).encode()
    assert call(port, "/api/send", body)[0] == 200
    app.job.join(10)
    s = json.loads(call(port, "/api/status")[1])
    assert s["running"] is False and s["result"]["sent"] == ["hr@acme.com"]
    code, out = call(port, "/api/send", body)                              # same list again: nobody left
    assert code == 400 and b"nobody left" in out
