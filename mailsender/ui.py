"""The screen: a page served on 127.0.0.1 and opened in the normal browser.

Every API call must carry a random key that is written only into the page itself,
and the Host header must be this machine. So another website open in the same
browser cannot make this server send mail, even though it listens on localhost.
"""

import http.server
import json
import os
import secrets
import signal
import threading
import time
import webbrowser
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

from . import campaign, oauth, recipients
from .accounts import CONFIG_DIR, PROVIDERS, Account, Accounts, slug
from .history import History
from .message import Template, Templates
from .transport import SendError, open_transport

HTML = Path(__file__).with_name("ui.html")
MASK = "••••••••"
MAX_UPLOAD = 20 * 1024 * 1024


class App:
    """Everything the screen can do, independent of HTTP (and so easy to test)."""

    def __init__(self, root: Path, config_dir: Path = CONFIG_DIR):
        self.root = root
        self.accounts = Accounts(config_dir)
        self.templates = Templates(root / "templates")
        self.history = History(root / "data" / "sent.db")
        self.uploads = root / "data" / "attachments"
        self.tokens = config_dir / "tokens"
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.job: threading.Thread | None = None
        self.status = {"running": False, "log": [], "sent": 0, "failed": 0, "total": 0, "result": None}

    # --- state ---------------------------------------------------------------

    def state(self) -> dict:
        accounts = []
        for a in self.accounts.all():
            d = a.public()
            d["sent_today"] = self.history.sent_today(a.email.lower())
            accounts.append(d)
        return {"accounts": accounts, "templates": self.templates.load_raw(),
                "providers": {k: {kk: v[kk] for kk in ("label", "host", "port", "security")}
                              for k, v in PROVIDERS.items()},
                "status": self.progress()}

    def progress(self) -> dict:
        with self.lock:
            s = dict(self.status)
            s["log"] = list(self.status["log"][-300:])
            return s

    # --- accounts --------------------------------------------------------------

    def save_account(self, d: dict) -> dict:
        d = {k: v for k, v in d.items() if k in Account.__dataclass_fields__}
        old = None
        if d.get("id"):
            try:
                old = self.accounts.get(d["id"])
            except KeyError:
                pass
        if old and d.get("password") in (MASK, None, ""):
            d["password"] = old.password  # the screen never gets the real password back
        d["id"] = d.get("id") or self.accounts.new_id(d.get("email", ""))
        a = Account(**d)
        self.accounts.save(a)
        return {"id": a.id, **self.state()}

    def delete_account(self, account_id: str) -> dict:
        self.accounts.delete(account_id)
        return self.state()

    def test_account(self, account_id: str) -> dict:
        a = self.accounts.get(account_id)
        tx = open_transport(a)
        try:
            tx.login()
        except SendError as e:
            return {"ok": False, "message": str(e)}
        finally:
            tx.close()
        how = "app password" if a.kind == "smtp" else "Gmail token"
        return {"ok": True, "message": f"Signed in to {a.email} with the {how}. Ready to send."}

    def create_token(self, client_secret: str, email: str) -> dict:
        out = self.tokens / f"{slug(email or 'gmail')}.json"
        try:
            path = oauth.authorize(Path(client_secret), out)
        except oauth.AuthError as e:
            return {"ok": False, "message": str(e)}
        return {"ok": True, "token_path": path, "message": f"Token saved to {path}"}

    # --- templates -------------------------------------------------------------

    def save_template(self, d: dict) -> dict:
        d = {k: v for k, v in d.items() if k in Template.__dataclass_fields__}
        d["id"] = d.get("id") or self.templates.new_id(d.get("name") or "message")
        d["name"] = (d.get("name") or "").strip() or d["id"]
        t = Template(**d)
        self.templates.save(t)
        return {"id": t.id, **self.state()}

    def delete_template(self, template_id: str) -> dict:
        f = self.templates.dir / f"{slug(template_id)}.json"
        f.unlink(missing_ok=True)
        return self.state()

    def upload(self, name: str, data: bytes, kind: str = "attachment") -> dict:
        """Keep a copy of a file chosen on the screen. Tokens and Google client files
        hold secrets, so they go to the private config folder, never into the project."""
        name = Path(name).name.strip() or "file"
        if len(data) > MAX_UPLOAD:
            raise ValueError("that file is over 20 MB")
        if kind in ("token", "client"):
            try:
                d = json.loads(data)
            except ValueError:
                raise ValueError("that is not a JSON file") from None
            if kind == "token" and not d.get("refresh_token"):
                raise ValueError("that file has no refresh_token: it is not a Gmail token.json")
            folder = self.tokens
            name = ("client-" if kind == "client" else "") + slug(Path(name).stem) + ".json"
        else:
            folder = self.uploads
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600 if folder == self.tokens else 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return {"path": str(path)}

    # --- recipients and sending ------------------------------------------------

    def check(self, text: str, cooldown_days: float) -> dict:
        c = recipients.check(text, self.history.recently_sent(float(cooldown_days or 0)),
                             rejected=self.history.rejected())
        return {"found": c.found, "send": c.send, "skipped": c.skipped, "reasons": c.summary()["skipped"]}

    def preview(self, account_id: str, template_id: str, to: str) -> dict:
        a, t = self.accounts.get(account_id), self.templates.get(template_id)
        return campaign.dry_run(a, t, [to or a.email])["preview"]

    def test_send(self, account_id: str, template_id: str) -> dict:
        """The real mail, to the sender's own address, so they can see it arrive."""
        a, t = self.accounts.get(account_id), self.templates.get(template_id)
        t = Template(**{**asdict(t), "subject": "[Test] " + t.subject})
        tx = open_transport(a)
        try:
            from .message import build
            tx.send(build(a, t, a.email))
        except SendError as e:
            return {"ok": False, "message": str(e)}
        finally:
            tx.close()
        return {"ok": True, "message": f"Test mail sent to {a.email}. Check that inbox (and Spam)."}

    def send(self, account_id: str, template_id: str, text: str, cooldown_days: float, gap: float) -> dict:
        with self.lock:
            if self.status["running"]:
                raise RuntimeError("a send is already running")
        a, t = self.accounts.get(account_id), self.templates.get(template_id)
        # Check again here, not only on the screen: a list checked an hour ago may
        # hold people who were mailed since.
        todo = recipients.check(text, self.history.recently_sent(float(cooldown_days or 0)),
                                rejected=self.history.rejected()).send
        if not todo:
            raise ValueError("nobody left to send to after the checks")
        campaign.dry_run(a, t, todo)  # a missing attachment fails now, not on mail 1
        self.stop.clear()
        with self.lock:
            self.status = {"running": True, "log": [], "sent": 0, "failed": 0, "total": len(todo),
                           "result": None, "current": "", "account": a.email, "template": t.name,
                           "started": time.time(), "gap": gap}

        def event(kind, **info):
            with self.lock:
                s = self.status
                if kind == "sending":
                    s["current"] = info["to"]
                elif kind == "sent":
                    s["sent"] += 1
                    s["log"].append(f"✓ {info['to']}")
                elif kind == "failed":
                    s["failed"] += 1
                    s["log"].append(f"✗ {info['to']}: {info['error']}")
                elif kind == "retry":
                    s["log"].append(f"… {info['to']}: {info['error']} - trying again in {info['seconds']}s")
                elif kind == "waiting":
                    s["current"] = f"waiting {info['seconds']}s before the next mail"
                elif kind == "done":
                    r = info["result"]
                    s["result"] = {"sent": r.sent, "failed": r.failed, "not_sent": r.not_sent,
                                   "stopped": r.stopped}
                    s["running"] = False
                    s["current"] = ""

        def work():
            try:
                campaign.run(a, t, todo, self.history, gap=gap, stop=self.stop, on_event=event)
            except Exception as e:  # never leave the screen stuck on "Sending…"
                with self.lock:
                    self.status["running"] = False
                    self.status["result"] = {"sent": [], "failed": [], "not_sent": [],
                                             "stopped": f"stopped by an error: {e}"}

        self.job = threading.Thread(target=work, daemon=True)
        self.job.start()
        return {"started": len(todo)}

    def stop_send(self) -> dict:
        self.stop.set()
        return {"ok": True}


def make_handler(app: App, key: str, port_holder: dict):
    routes = {
        "/api/state": lambda d: app.state(),
        "/api/status": lambda d: app.progress(),
        "/api/account/save": app.save_account,
        "/api/account/delete": lambda d: app.delete_account(d["id"]),
        "/api/account/test": lambda d: app.test_account(d["id"]),
        "/api/token/create": lambda d: app.create_token(d.get("client_secret", ""), d.get("email", "")),
        "/api/template/save": app.save_template,
        "/api/template/delete": lambda d: app.delete_template(d["id"]),
        "/api/check": lambda d: app.check(d.get("text", ""), d.get("cooldown_days", 30)),
        "/api/preview": lambda d: app.preview(d["account"], d["template"], d.get("to", "")),
        "/api/test-send": lambda d: app.test_send(d["account"], d["template"]),
        "/api/send": lambda d: app.send(d["account"], d["template"], d.get("text", ""),
                                        d.get("cooldown_days", 30), d.get("gap", 45)),
        "/api/stop": lambda d: app.stop_send(),
        "/api/history": lambda d: {"rows": app.history.recent()},
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def _host_ok(self) -> bool:
            port = port_holder["port"]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _reply(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._host_ok():
                return self._reply(403, b"wrong host")
            if urlparse(self.path).path != "/":
                return self._reply(404, b"not found", "text/plain")
            boot = f"<script>window.__KEY__={json.dumps(key)};</script>"
            page = HTML.read_text().replace("<script>", boot + "\n<script>", 1)
            self._reply(200, page.encode(), "text/html; charset=utf-8")

        def do_POST(self):
            if not self._host_ok() or not secrets.compare_digest(self.headers.get("X-Key", ""), key):
                return self._reply(403, b'{"error": "not allowed"}')
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_UPLOAD + 1024:
                return self._reply(413, b'{"error": "too big"}')
            raw = self.rfile.read(length)
            try:
                if path == "/api/upload":
                    out = app.upload(self.headers.get("X-Filename", ""), raw, self.headers.get("X-Kind", ""))
                elif path in routes:
                    out = routes[path](json.loads(raw or b"{}"))
                else:
                    return self._reply(404, b'{"error": "no such call"}')
            except (KeyError, ValueError, RuntimeError, TypeError) as e:
                msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
                return self._reply(400, json.dumps({"error": msg}).encode())
            self._reply(200, json.dumps(out).encode())

        def log_message(self, *a):
            pass

    return Handler


def serve(root: Path, port: int = 8765, open_browser: bool = True, config_dir: Path = CONFIG_DIR):
    app = App(root, config_dir)
    key = secrets.token_urlsafe(32)
    holder = {"port": port}
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, key, holder))
    except OSError:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, key, holder))
    holder["port"] = server.server_address[1]
    url = f"http://127.0.0.1:{holder['port']}/"
    print(f"The mail sender is open at {url}  (Ctrl+C or ./stop.sh to quit)", flush=True)
    if open_browser:
        webbrowser.open(url)

    def quit_(*_):
        raise KeyboardInterrupt  # ./stop.sh sends SIGTERM: end the same way as Ctrl+C

    signal.signal(signal.SIGTERM, quit_)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop.set()
        server.server_close()
        # Let a mail that is on its way finish and be written to the history;
        # killing it mid-send could mail that person twice later.
        if app.job and app.job.is_alive():
            print("Finishing the current mail before quitting…", flush=True)
            app.job.join(timeout=120)
