"""mailsend - send one message to a list of addresses, from your own mail account.

    mailsend                                   open the screen in your browser
    mailsend add-account                       add an account (app password or Gmail token)
    mailsend accounts                          list accounts
    mailsend test ACCOUNT                      check that the account can sign in
    mailsend auth --client-secret FILE         create a Gmail token.json (send-only)
    mailsend templates                         list saved messages
    mailsend check FILE                        show who would be mailed, and who is skipped and why
    mailsend send -a ACCOUNT -t MESSAGE FILE   dry run: builds every mail, sends nothing
    mailsend send ... --live                   really send
    mailsend history                           the last mails sent

FILE may be '-' to read the addresses from stdin, e.g.
    harvest export --days 1 | mailsend send -a me -t java - --live
"""

import argparse
import getpass
import signal
import sys
import threading
from pathlib import Path

from . import campaign, oauth, recipients
from .accounts import CONFIG_DIR, PROVIDERS, Account, Accounts, guess_provider, slug
from .history import History
from .message import Templates

ROOT = Path.cwd()


def _read(file: str) -> str:
    return sys.stdin.read() if file == "-" else Path(file).read_text()


def _ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    return input(f"{prompt}{shown}: ").strip() or default


def add_account(accounts: Accounts) -> None:
    print("New account. Press Enter to accept a [default].\n")
    email = _ask("Your email address")
    name = _ask("Your name, shown as the sender (optional)")
    kind = _ask("Sign in with (1) app password or (2) Gmail token.json", "1")
    d = {"email": email, "name": name, "id": accounts.new_id(email)}
    if kind.startswith("2"):
        d |= {"kind": "gmail_token", "token_path": _ask("Path to token.json")}
    else:
        p = PROVIDERS.get(guess_provider(email) or "")
        if p:
            print(f"Mail server: {p['host']}:{p['port']} ({p['security']})")
            d |= {"host": p["host"], "port": p["port"], "security": p["security"]}
        else:
            d |= {"host": _ask("SMTP server (e.g. smtp.example.com)"),
                  "port": int(_ask("Port", "587")),
                  "security": _ask("Security: starttls, ssl or none", "starttls")}
        if p and p["host"] == "smtp.gmail.com":
            print("Gmail needs an app password: myaccount.google.com/apppasswords (2-Step Verification on).")
        d |= {"kind": "smtp", "password": getpass.getpass("App password (hidden): ")}
    a = Account(**d)
    accounts.save(a)
    print(f"\nSaved as '{a.id}' in {accounts.path} (readable only by you).")
    test(a)


def test(a: Account) -> bool:
    from .transport import SendError, open_transport
    tx = open_transport(a)
    try:
        tx.login()
        print(f"✓ {a.email} signed in. Ready to send.")
        return True
    except SendError as e:
        print(f"✗ {a.email}: {e}")
        return False
    finally:
        tx.close()


def send(args, accounts: Accounts, templates: Templates, history: History) -> int:
    a, t = accounts.get(args.account), templates.get(args.template)
    c = recipients.check(_read(args.file), history.recently_sent(args.cooldown), rejected=history.rejected())
    _print_check(c)
    if not c.send:
        return 0
    if not args.live:
        out = campaign.dry_run(a, t, c.send)
        p = out["preview"]
        print(f"\nDRY RUN - nothing was sent. The first of {out['count']} mails:\n")
        print(f"From:    {p['from']}\nTo:      {p['to']}\nSubject: {p['subject']}")
        if p["attachments"]:
            print(f"Attached: {', '.join(p['attachments'])}")
        print("\n" + p["body"])
        print(f"\nAdd --live to send these {out['count']} mails from {a.email} (gap {args.gap:g}s).")
        return 0

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: (print("\nStopping after this mail…"), stop.set()))

    def event(kind, **i):
        if kind == "sent":
            print(f"[{i['index'] + 1}/{i['total']}] sent    {i['to']}", flush=True)
        elif kind == "failed":
            print(f"[{i['index'] + 1}/{i['total']}] FAILED  {i['to']}: {i['error']}", flush=True)
        elif kind == "retry":
            print(f"          retry   {i['to']} in {i['seconds']}s: {i['error']}", flush=True)

    r = campaign.run(a, t, c.send, history, gap=args.gap, stop=stop, on_event=event)
    print(f"\ndone: {len(r.sent)} sent, {len(r.failed)} failed, {len(r.not_sent)} not sent"
          + (f" - {r.stopped}" if r.stopped else ""))
    left = r.not_sent + [e for e, _ in r.failed]
    if left:
        out = ROOT / "data" / "not_sent.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(left) + "\n")
        print(f"The {len(left)} not sent are in {out}; send that file again later.")
    return 1 if r.failed or (r.stopped and r.not_sent) else 0


def _print_check(c: recipients.Checked) -> None:
    print(f"{c.found} address{'es' if c.found != 1 else ''} found · {len(c.send)} to send · "
          f"{len(c.skipped)} left out")
    for e, why in c.skipped:
        print(f"  skip  {e:40} {why}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="mailsend", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    u = sub.add_parser("ui", help="open the screen (the default)")
    u.add_argument("--port", type=int, default=8765)
    u.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    sub.add_parser("add-account", help="add an account interactively")
    sub.add_parser("accounts", help="list accounts")
    t = sub.add_parser("test", help="check that an account can sign in")
    t.add_argument("account")
    au = sub.add_parser("auth", help="create a Gmail token.json from a client_secret.json")
    au.add_argument("--client-secret", required=True, type=Path)
    au.add_argument("--out", type=Path, help="where to write the token (default: the private config folder)")
    sub.add_parser("templates", help="list saved messages")
    ch = sub.add_parser("check", help="who would be mailed, and who is skipped and why")
    ch.add_argument("file")
    ch.add_argument("--cooldown", type=float, default=30, help="skip anyone mailed in the last N days (0 = off)")
    s = sub.add_parser("send", help="send a message to a list (dry run unless --live)")
    s.add_argument("file", help="text file with addresses, or '-' for stdin")
    s.add_argument("-a", "--account", required=True)
    s.add_argument("-t", "--template", required=True, help="saved message id (see `mailsend templates`)")
    s.add_argument("--gap", type=float, default=45, help="seconds between mails (default 45, minimum 2)")
    s.add_argument("--cooldown", type=float, default=30, help="skip anyone mailed in the last N days (0 = off)")
    s.add_argument("--live", action="store_true", help="really send (without it, nothing is sent)")
    h = sub.add_parser("history", help="the last mails sent")
    h.add_argument("--limit", type=int, default=50)
    args = ap.parse_args(argv)

    accounts, templates = Accounts(CONFIG_DIR), Templates(ROOT / "templates")
    if args.cmd in (None, "ui"):
        from .ui import serve
        return serve(ROOT, port=getattr(args, "port", 8765), open_browser=not getattr(args, "no_browser", False))
    try:
        if args.cmd == "add-account":
            return add_account(accounts)
        if args.cmd == "accounts":
            for a in accounts.all():
                how = "app password" if a.kind == "smtp" else f"Gmail token ({a.token_path})"
                print(f"{a.id:20} {a.email:34} {how}")
            return
        if args.cmd == "test":
            sys.exit(0 if test(accounts.get(args.account)) else 1)
        if args.cmd == "auth":
            out = args.out or CONFIG_DIR / "tokens" / f"{slug(args.client_secret.stem)}-token.json"
            print("A browser tab opens. Sign in and allow 'Send email on your behalf'.")
            print(f"Token saved to {oauth.authorize(args.client_secret, out)}")
            print("Add it with `mailsend add-account` (choose 2, Gmail token).")
            return
        if args.cmd == "templates":
            for t in templates.all():
                print(f"{t.id:20} {t.subject}" + (f"  (+{len(t.attachments)} file)" if t.attachments else ""))
            return
        history = History(ROOT / "data" / "sent.db")
        if args.cmd == "check":
            return _print_check(recipients.check(_read(args.file), history.recently_sent(args.cooldown),
                                                 rejected=history.rejected()))
        if args.cmd == "send":
            sys.exit(send(args, accounts, templates, history))
        if args.cmd == "history":
            for r in history.recent(args.limit):
                print(f"{r['at']}  {r['status']:6}  {r['email']:40} {r['error'] or ''}")
    except (KeyError, ValueError, oauth.AuthError) as e:
        sys.exit(e.args[0] if isinstance(e, KeyError) and e.args else str(e))


if __name__ == "__main__":
    main()
