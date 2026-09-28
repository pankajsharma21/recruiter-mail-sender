"""Real SMTP round-trips against the fake server: what arrives, and what the loop does
when the login, an address, the line or the daily limit goes wrong."""

import threading

from conftest import smtp_account
from fakesmtp import FakeSMTP
from mailsender import campaign, recipients


def test_every_mail_arrives_with_its_attachment(smtp, fast, history, template):
    a = smtp_account(smtp)
    r = campaign.run(a, template, ["hr@acme-tech.co.in", "jobs@gmail.com"], history, gap=0)
    assert r.sent == ["hr@acme-tech.co.in", "jobs@gmail.com"] and not r.failed and not r.stopped
    first, second = smtp.messages
    assert first["To"] == "hr@acme-tech.co.in"
    assert first["From"] == "Test Me <me@test.example>"
    assert first["Subject"] == "Application for Acme Tech"
    assert second["Subject"] == "Application for your company"      # a Gmail address has no company
    body = first.get_body(("plain",)).get_content()
    assert "work at Acme Tech. {not a placeholder}" in body          # other braces left alone
    (att,) = list(first.iter_attachments())
    assert att.get_filename() == "Resume.pdf" and att.get_content() == b"%PDF-1.4 fake resume"
    assert smtp.logins == 1                                           # one connection for the whole list


def test_sent_addresses_are_skipped_next_time(smtp, fast, history, template):
    campaign.run(smtp_account(smtp), template, ["hr@acme.com"], history, gap=0)
    c = recipients.check("hr@acme.com, new@beta.com", history.recently_sent(30))
    assert c.send == ["new@beta.com"]
    assert c.skipped[0][0] == "hr@acme.com" and c.skipped[0][1].startswith("already mailed")
    assert recipients.check("hr@acme.com", history.recently_sent(0)).send == ["hr@acme.com"]  # cooldown off


def test_wrong_password_stops_at_once_and_marks_nobody(smtp, fast, history, template):
    a = smtp_account(smtp, password="wrong")
    r = campaign.run(a, template, ["a@x.com", "b@y.com"], history, gap=0)
    assert r.sent == [] and r.failed == [] and r.not_sent == ["a@x.com", "b@y.com"]
    assert "login refused" in r.stopped
    assert history.recently_sent(30) == {}


def test_a_refused_address_fails_alone(smtp, fast, history, template):
    r = campaign.run(smtp_account(smtp), template, ["a@x.com", "refuse@y.com", "c@z.com"], history, gap=0)
    assert r.sent == ["a@x.com", "c@z.com"]
    assert [e for e, _ in r.failed] == ["refuse@y.com"] and "550" in r.failed[0][1]
    assert not r.stopped


def test_a_busy_address_is_retried_then_given_up(smtp, fast, history, template):
    events = []
    r = campaign.run(smtp_account(smtp), template, ["busy@x.com", "ok@y.com"], history, gap=0,
                     on_event=lambda kind, **i: events.append(kind))
    assert events.count("retry") == 2
    assert [e for e, _ in r.failed] == ["busy@x.com"] and r.sent == ["ok@y.com"]


def test_line_down_stops_after_three_in_a_row(fast, history, template):
    s = FakeSMTP(drop_all=True)
    try:
        todo = [f"p{i}@x.com" for i in range(6)]
        r = campaign.run(smtp_account(s), template, todo, history, gap=0)
    finally:
        s.close()
    assert len(r.failed) == 3 and r.not_sent == todo[3:]
    assert "failed in a row" in r.stopped
    assert history.recently_sent(30) == {}                            # nothing counted as sent


def test_gmail_style_daily_cap_stops_the_run(fast, history, template):
    s = FakeSMTP(limit_after=2)
    try:
        r = campaign.run(smtp_account(s), template, [f"p{i}@x.com" for i in range(5)], history, gap=0)
    finally:
        s.close()
    assert len(r.sent) == 2 and r.failed == [] and len(r.not_sent) == 3
    assert "sending limit" in r.stopped


def test_own_daily_limit_holds_the_rest_for_tomorrow(smtp, fast, history, template):
    a = smtp_account(smtp, daily_limit=2)
    r = campaign.run(a, template, ["a@x.com", "b@x.com", "c@x.com"], history, gap=0)
    assert r.sent == ["a@x.com", "b@x.com"] and r.not_sent == ["c@x.com"] and "tomorrow" in r.stopped
    r2 = campaign.run(a, template, ["c@x.com"], history, gap=0)
    assert r2.sent == [] and r2.not_sent == ["c@x.com"] and "daily limit reached" in r2.stopped


def test_stop_during_the_gap(smtp, history, template, monkeypatch):
    monkeypatch.setattr(campaign, "MIN_GAP", 0)
    stop = threading.Event()

    def on_event(kind, **i):
        if kind == "sent":
            stop.set()                    # the person presses Stop right after the first mail

    r = campaign.run(smtp_account(smtp), template, ["a@x.com", "b@x.com", "c@x.com"], history,
                     gap=30, stop=stop, on_event=on_event)
    assert r.sent == ["a@x.com"] and r.not_sent == ["b@x.com", "c@x.com"] and r.stopped == "Stopped by you."


def test_dry_run_sends_nothing(smtp, history, template):
    out = campaign.dry_run(smtp_account(smtp), template, ["hr@acme.com", "b@x.com"])
    assert out["count"] == 2 and out["preview"]["subject"] == "Application for Acme"
    assert out["preview"]["attachments"] == ["Resume.pdf"]
    assert smtp.messages == []


def test_a_dead_address_is_left_out_next_time(smtp, fast, history, template):
    campaign.run(smtp_account(smtp), template, ["refuse@gone.com", "ok@x.com"], history, gap=0)
    c = recipients.check("refuse@gone.com ok2@x.com", history.recently_sent(30), rejected=history.rejected())
    assert c.send == ["ok2@x.com"]
    assert c.skipped[0][0] == "refuse@gone.com" and c.skipped[0][1].startswith("refused before: address refused: 550")


def test_dead_addresses_in_a_row_do_not_stop_the_run(smtp, fast, history, template):
    todo = ["refuse1@x.com", "refuse2@x.com", "refuse3@x.com", "refuse4@x.com", "ok@y.com"]
    r = campaign.run(smtp_account(smtp), template, todo, history, gap=0)
    assert r.sent == ["ok@y.com"] and len(r.failed) == 4 and not r.stopped


def test_a_busy_address_is_not_marked_dead(smtp, fast, history, template):
    campaign.run(smtp_account(smtp), template, ["busy@x.com"], history, gap=0)
    assert history.rejected() == {}          # 451 = try again later, so it stays on the list
