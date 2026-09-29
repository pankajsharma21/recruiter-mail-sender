"""A tiny real SMTP server for tests: speaks the protocol over a socket, checks the
login, and keeps what it receives. Addresses containing "refuse" get a permanent
550, "busy" a temporary 451; `limit_after` makes it answer like Gmail's daily cap.

tls="starttls" behaves like Gmail on 587 (no login before STARTTLS); tls="ssl" is
encrypted from the first byte, like port 465. Both need `certfile`/`keyfile`."""

import base64
import socketserver
import ssl
import threading
from email import message_from_bytes, policy


class FakeSMTP:
    def __init__(self, user="me@test.example", password="secret", limit_after=None, drop_all=False,
                 tls=None, certfile=None, keyfile=None):
        self.user, self.password = user, password
        self.tls = tls
        self.raw: list[bytes] = []  # each message exactly as it came over the wire
        self.tls_used: list[str] = []
        if tls:
            # Built directly, not with create_default_context, which tests patch for the client side.
            self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.ctx.load_cert_chain(certfile, keyfile)
        self.limit_after = limit_after
        self.drop_all = drop_all
        self.messages: list = []   # parsed EmailMessage objects
        self.rcpts: list[str] = []
        self.logins = 0
        outer = self

        class Handler(socketserver.StreamRequestHandler):
            def reply(self, line):
                self.wfile.write((line + "\r\n").encode())

            def secure(self):
                self.connection = outer.ctx.wrap_socket(self.connection, server_side=True)
                self.rfile = self.connection.makefile("rb")
                self.wfile = self.connection.makefile("wb", buffering=0)
                outer.tls_used.append(self.connection.version())

            def handle(self):
                if outer.drop_all:
                    return  # accept the TCP connection, then hang up: looks like a network drop
                encrypted = False
                if outer.tls == "ssl":
                    try:
                        self.secure()
                    except (ssl.SSLError, OSError):
                        return  # the client refused our certificate
                    encrypted = True
                self.reply("220 fake ESMTP")
                authed, rcpts = False, []
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode().rstrip("\r\n")
                    cmd = line.split(" ", 1)[0].upper()
                    if cmd in ("EHLO", "HELO"):
                        if outer.tls == "starttls" and not encrypted:
                            self.reply("250-fake"); self.reply("250 STARTTLS")   # AUTH only after TLS, like Gmail
                        else:
                            self.reply("250-fake"); self.reply("250 AUTH PLAIN LOGIN")
                    elif cmd == "STARTTLS":
                        self.reply("220 go ahead")
                        try:
                            self.secure()
                        except (ssl.SSLError, OSError):
                            return
                        encrypted = True
                    elif cmd == "AUTH" and outer.tls and not encrypted:
                        self.reply("530 5.7.0 Must issue a STARTTLS command first")
                    elif cmd == "AUTH":
                        parts = line.split()
                        if parts[1].upper() == "PLAIN":
                            blob = parts[2] if len(parts) > 2 else None
                            if blob is None:
                                self.reply("334 "); blob = self.rfile.readline().decode().strip()
                            _, u, p = base64.b64decode(blob).decode().split("\0")
                        else:  # LOGIN
                            self.reply("334 VXNlcm5hbWU6")
                            u = base64.b64decode(self.rfile.readline().strip()).decode()
                            self.reply("334 UGFzc3dvcmQ6")
                            p = base64.b64decode(self.rfile.readline().strip()).decode()
                        if (u, p) == (outer.user, outer.password):
                            authed = True; outer.logins += 1; self.reply("235 ok")
                        else:
                            self.reply("535 5.7.8 Username and Password not accepted")
                    elif cmd == "MAIL":
                        if not authed:
                            self.reply("530 auth first"); continue
                        if outer.limit_after is not None and len(outer.messages) >= outer.limit_after:
                            self.reply("550 5.4.5 Daily user sending limit exceeded"); continue
                        rcpts = []; self.reply("250 ok")
                    elif cmd == "RCPT":
                        addr = line.split(":", 1)[1].strip().strip("<>")
                        if "refuse" in addr:
                            self.reply("550 5.1.1 no such user")
                        elif "busy" in addr:
                            self.reply("451 4.3.0 try again later")
                        else:
                            rcpts.append(addr); self.reply("250 ok")
                    elif cmd == "DATA":
                        self.reply("354 go")
                        data = b""
                        while True:
                            l = self.rfile.readline()
                            if l in (b".\r\n", b".\n", b""):
                                break
                            data += l[1:] if l.startswith(b"..") else l
                        outer.raw.append(data)
                        outer.messages.append(message_from_bytes(data, policy=policy.default))
                        outer.rcpts += rcpts
                        self.reply("250 queued")
                    elif cmd == "RSET":
                        rcpts = []; self.reply("250 ok")
                    elif cmd == "NOOP":
                        self.reply("250 ok")
                    elif cmd == "QUIT":
                        self.reply("221 bye"); return
                    else:
                        self.reply("502 not implemented")

        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
