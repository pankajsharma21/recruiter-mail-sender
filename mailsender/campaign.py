"""The send loop: one mail at a time, with a gap, a daily limit, retries and a Stop.

Nothing is marked as sent unless the server accepted it, so after a stop, a crash
or a lost connection the same list can simply be sent again: the cooldown skips
everyone who already got the mail.
"""

import random
import threading
from dataclasses import dataclass, field

from .accounts import Account
from .history import History
from .message import Template, build
from .transport import SendError, open_transport

RETRY_WAITS = (20, 60)        # seconds before the 2nd and 3rd try of a temporary failure
MAX_FAILS_IN_A_ROW = 3        # this many addresses failing back to back = the line is down
MIN_GAP = 2


@dataclass
class Result:
    sent: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    not_sent: list[str] = field(default_factory=list)
    stopped: str = ""          # why it ended early, or "" when it went through the list


def run(account: Account, template: Template, recipients: list[str], history: History, *,
        gap: float = 45, stop: threading.Event | None = None, on_event=lambda kind, **info: None,
        transport=None, jitter: float = 0.2) -> Result:
    """Send `template` to every address. `on_event(kind, **info)` reports progress:
    "sending", "sent", "failed", "retry", "waiting", "done"."""
    stop = stop or threading.Event()
    gap = max(MIN_GAP, float(gap))
    res = Result()
    sender = account.email.lower()

    room = account.daily_limit - history.sent_today(sender)
    todo = list(recipients)
    if room <= 0:
        res.not_sent, res.stopped = todo, (f"daily limit reached: {account.daily_limit} mails in the last "
                                           f"24 hours from {account.email}. Try again tomorrow.")
        on_event("done", result=res)
        return res
    if len(todo) > room:
        res.not_sent = todo[room:]
        todo = todo[:room]

    tx = transport or open_transport(account)
    fails_in_a_row = 0
    try:
        for i, to in enumerate(todo):
            if stop.is_set():
                res.not_sent = todo[i:] + res.not_sent
                res.stopped = "Stopped by you."
                break
            on_event("sending", to=to, index=i, total=len(todo))
            error = _send_with_retries(tx, account, template, to, stop, on_event)
            if error is None:
                res.sent.append(to)
                history.log(to, sender, template.id, template.subject, "sent")
                fails_in_a_row = 0
                on_event("sent", to=to, index=i, total=len(todo))
            else:
                msg, kind = error
                if kind in ("account", "stopped"):
                    # A refused login fails every other address the same way; a stop is a stop.
                    res.not_sent = todo[i:] + res.not_sent
                    res.stopped = msg
                    break
                res.failed.append((to, msg))
                # "rejected" = the address itself is dead (e.g. 550 no such user): remembered,
                # so it is left out next time instead of failing again and again.
                history.log(to, sender, template.id, template.subject,
                            "rejected" if kind == "rejected" else "failed", msg)
                on_event("failed", to=to, error=msg, index=i, total=len(todo))
                # Only network-type failures mean the line is down; a dead address does not.
                fails_in_a_row = fails_in_a_row + 1 if kind == "temporary" else 0
                if fails_in_a_row >= MAX_FAILS_IN_A_ROW:
                    res.not_sent = todo[i + 1:] + res.not_sent
                    res.stopped = (f"{fails_in_a_row} addresses failed in a row ({msg}). Stopped so the rest "
                                   "are not wasted; check the internet or the account and send again.")
                    break
            if i < len(todo) - 1:
                wait = gap * random.uniform(1 - jitter, 1 + jitter)
                on_event("waiting", seconds=round(wait))
                if stop.wait(wait):
                    res.not_sent = todo[i + 1:] + res.not_sent
                    res.stopped = "Stopped by you."
                    break
        else:
            if res.not_sent and not res.stopped:
                res.stopped = (f"daily limit: sent {len(res.sent)}, the other {len(res.not_sent)} "
                               "can go tomorrow")
    finally:
        tx.close()
    on_event("done", result=res)
    return res


def _send_with_retries(tx, account, template, to, stop, on_event):
    """None when sent, else (reason, kind): kind is "account" (the login or the account
    is the problem), "rejected" (this address is refused for good), "temporary" (still
    failing after the retries) or "stopped"."""
    msg = build(account, template, to)
    last = ""
    for attempt, wait in enumerate((0, *RETRY_WAITS)):
        if wait:
            on_event("retry", to=to, attempt=attempt + 1, seconds=wait, error=last)
            if stop.wait(wait):
                return "Stopped by you.", "stopped"
        try:
            tx.send(msg)
            return None
        except SendError as e:
            last = str(e)
            if e.account:
                return last, "account"
            if not e.temporary:
                return last, "rejected"
    return last, "temporary"


def dry_run(account: Account, template: Template, recipients: list[str]) -> dict:
    """Build every message without sending: catches a missing attachment or a bad
    address before the first real mail goes out."""
    first = build(account, template, recipients[0]) if recipients else None
    for to in recipients[1:]:
        build(account, template, to)
    return {"count": len(recipients), "preview": preview(first) if first else None}


def preview(msg) -> dict:
    body = msg.get_body(preferencelist=("plain",))
    files = [p.get_filename() for p in msg.iter_attachments()]
    return {"from": msg["From"], "to": msg["To"], "subject": msg["Subject"],
            "body": body.get_content() if body else "", "attachments": files}
