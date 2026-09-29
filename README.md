# Recruiter Mail Sender

**Send one message (say, your job application with your resume attached) to a list of email addresses, from your own mail account. Paste the list, press Send. It waits between mails, stays under your daily limit, and never mails the same person twice.**

It signs in with an **app password** (Gmail, Outlook, Yahoo, Zoho or any other provider) or a **Gmail token** (`token.json`). It runs on your own computer, uses nothing but Python, and needs no paid service or account with this project.

It pairs with [Recruiter Mail Harvester](https://github.com/pankajsharma21/recruiter-mail-harvester), which collects recruiter addresses from LinkedIn and Naukri, but any list works: a spreadsheet column, a WhatsApp forward, an email.

---

## What it does

1. **You add your account once**: your address and an app password, or a Gmail token. It checks the sign-in right away.
2. **You write the message once**: subject, text and attachments. `{company}` in the text becomes the company from each address (`hr@acme-tech.co.in` → *Acme Tech*). Save as many messages as you like.
3. **You paste the addresses**, in any shape. It picks them out and leaves out, with the reason for each:
   - duplicates,
   - anyone you mailed in the last 30 days (you choose the number),
   - addresses a server refused before (*550 no such user*),
   - `noreply@` addresses,
   - typos like `@gamil.com` (it suggests the fix and doesn't guess).
4. **It sends one mail at a time**, 45 seconds apart by default, with a progress bar and a **■ Stop** button. Nothing is marked as sent unless the mail server accepted it, so you can always just send the same list again.

## Install

You need **Python 3.11 or newer**.

```bash
git clone https://github.com/pankajsharma21/recruiter-mail-sender.git
cd recruiter-mail-sender
python3 -m venv .venv
.venv/bin/pip install -e .
```

On Windows, use `python` instead of `python3` and `.venv\Scripts\` instead of `.venv/bin/`.

## Get an app password (2 minutes)

Your normal password will not work; mail providers want a separate *app password* for tools like this.

| Provider | Where |
|---|---|
| **Gmail** | Turn on 2-Step Verification, then open **myaccount.google.com/apppasswords**, type any name (e.g. "mail sender") and copy the 16 letters. |
| **Outlook / Hotmail** | account.microsoft.com → Security → Advanced security options → App passwords (needs two-step verification). Microsoft is phasing out app passwords for personal Outlook.com accounts, so the sign-in may be refused; **Test sign-in** tells you at once. |
| **Yahoo** | Account info → Account security → Generate app password. |
| **Zoho** | Security → App passwords in your Zoho account (`smtp.zoho.com`, or `smtp.zoho.in` for `@zohomail.in`). |
| **Company mail** | Ask IT for the SMTP server, port and whether to use STARTTLS (587) or SSL (465). |

For Gmail, Outlook, Yahoo and Zoho the mail server is filled in for you. For any other address the screen shows the server fields.

### Or use a Gmail token instead

If you already have a Gmail `token.json` (for example from another tool), choose it on the screen. It is copied to a private folder.

If you don't have one, you can create one:
1. In Google Cloud Console, create a project and enable the **Gmail API**.
2. Go to **Credentials → Create credentials → OAuth client ID → Desktop app**, and download the JSON file.
3. On the screen, choose that file under *No token yet?* and press **Create token**, or run `mailsend auth --client-secret client_secret.json`.

Google then asks you to allow **send email** only: the token can send mail but never read it.

## Use

### 1. Start it

| Your computer | Start | Stop |
|---|---|---|
| Linux / macOS, background (easiest) | `./run.sh` | `./stop.sh` |
| Linux / macOS, in the terminal | `.venv/bin/mailsend` | Ctrl+C |
| Windows | `.venv\Scripts\mailsend` | Ctrl+C |

The screen opens in your browser (at `http://127.0.0.1:8765/`). `./run.sh` prints the address too, in case the tab didn't open. Its output goes to `logs/mailsend.log`.

Stopping while a mail is on its way lets that mail finish and be recorded first, so nobody gets it twice.

### 2. On the screen

1. **Send from:** press **+ Add account**, fill in your address and app password (or choose the Gmail token), and press **Save account**. It signs in right away and says ✓ or explains what is wrong.
2. **Message:** press **+ New message**, write the subject and text, attach your resume, and press **Save message**.
3. **Send to:** paste the addresses and press **Check list**. You see how many will be mailed, and why each one left out was left out.
4. **Send:**
   - **Preview** shows the first mail exactly as it will go out.
   - **Send a test to myself** sends the real mail to your own address, so you can check it in your inbox.
   - **Send to N people** asks you to click once more to confirm, then sends. **■ Stop** ends it after the current mail, and the addresses not sent are listed so you can copy them.

Next time your account and message are already selected: paste, check, send.

### Step by step: your first mail

Follow these once from start to finish. After that it's just steps 4 to 7.

**1. Install** (only the first time):
```bash
git clone https://github.com/pankajsharma21/recruiter-mail-sender.git
cd recruiter-mail-sender
python3 -m venv .venv
.venv/bin/pip install -e .
```

**2. Get an app password** (only the first time). For Gmail:
1. Open **myaccount.google.com/security** and turn on **2-Step Verification**.
2. Open **myaccount.google.com/apppasswords**, type a name such as `mail sender`, and press **Create**.
3. Copy the 16 letters it shows (`abcd efgh ijkl mnop`). Google shows them only once.

Other providers: see [Get an app password](#get-an-app-password-2-minutes).

**3. Start the screen:**
```bash
cd recruiter-mail-sender
./run.sh
```
The screen opens in your browser. If it doesn't, open the address `./run.sh` prints (normally `http://127.0.0.1:8765/`).

**4. Add your account** (only the first time):
1. Under **1 Send from**, press **+ Add account**.
2. Fill in your email address and your name. Keep **App password** selected and paste the 16 letters.
3. Press **Save account**. It signs in right away: *✓ Signed in … Ready to send.* If you see ✗, the message says what to fix.

**5. Write the message** (only the first time, or when you want a new one):
1. Under **2 Message**, press **+ New message**.
2. Give it a name (only you see it), a subject and the text. `{company}` becomes the company from each address.
3. Under *Attachments*, choose your resume.
4. Press **Save message**.

**6. Test it on yourself:** under **4 Send**, press **Send a test to myself**. Open your inbox (and Spam): the mail is there with *[Test]* in the subject and your resume attached. Check that it looks right before mailing anyone else.

**7. Send to the list:**
1. Under **3 Send to**, paste the addresses and press **Check list**. To see the checks at work, try this:
   ```
   HR: hr@acme-tech.co.in, HR@acme-tech.co.in
   careers [at] infosys [dot] com
   noreply@naukri.com  priya@gamil.com  jobs@wipro.com
   ```
   It keeps 3 and leaves out 3: the duplicate, the no-reply address and the `gamil.com` typo. Open *Why each one was left out* to see the reasons.
2. Press **Preview** to see the first mail exactly as it will go out.
3. Press **Send to N people**, then click it once more to confirm.
4. Watch the progress. **■ Stop** ends it after the current mail. When it finishes, the addresses not sent are listed so you can copy them for later.

**8. Stop the screen:** `./stop.sh` (or Ctrl+C if you started it with `.venv/bin/mailsend`).

Next time: `./run.sh`, paste the new list, **Check list**, **Send**. Your account and message are already selected, and everyone you mailed in the last 30 days is left out automatically.

## Good to know

- **Your password stays on your computer.** Accounts are saved in `~/.config/recruiter-mail-sender/accounts.json`, readable only by you (file mode 600), together with any Gmail tokens. Nothing is ever put in the project folder, and the screen is never sent the password back.
- **The screen only answers your own browser.** It listens on `127.0.0.1` only, and every request must carry a random key that is written into the page. Another website open in the same browser cannot use it to send mail.
- **Keep the 45-second gap.** Sending fast looks like spam and can get an account blocked. Free Gmail allows about **500 mails a day**; the default daily limit here is 400 per account (you can change it). When the limit is reached, the rest wait for tomorrow.
- **What happens when something goes wrong:**
  - *Wrong password or revoked token:* it stops at once.
  - *A refused address:* that one fails and is left out of future sends.
  - *A network drop:* each mail is tried 3 times. If 3 addresses fail in a row, it stops and keeps the rest, rather than burning through the list.
- Your messages, attachments and send history stay in `templates/` and `data/`, which are git-ignored.
- These are real people's addresses. Send them something they would want: an application for the job they posted.

## If something goes wrong

| What you see | What to do |
|---|---|
| `Gmail refused the login … Use a 16-letter app password` | You used your normal password, or 2-Step Verification is off. Create an app password (see above). |
| `cannot reach smtp…: no internet, or the server name is wrong` | Check the connection, or the SMTP server under *Mail server*. |
| `Google no longer accepts this token` | The token was revoked or expired. Create a new one. |
| `daily limit reached` | You hit the account's daily limit. The rest can go tomorrow. |
| The browser tab didn't open | Open the address printed by `./run.sh` (also in `logs/mailsend.log`). |
| `Already running` but no tab | `./stop.sh`, then `./run.sh`. |
| Mail lands in Spam | Send a test to yourself first, keep the gap at 45 s, and write like a person (no ALL CAPS subject, a real signature). |

## Command line

Everything the screen does is also a command. Sends are a **dry run unless you add `--live`**.

| Command | What it does |
|---|---|
| `mailsend` | Opens the screen (same as `mailsend ui`) |
| `mailsend add-account` | Add an account by answering questions (the password is typed hidden) |
| `mailsend accounts` / `mailsend templates` | List saved accounts / messages |
| `mailsend test ACCOUNT` | Check that an account can sign in |
| `mailsend auth --client-secret FILE` | Create a Gmail token (send-only) |
| `mailsend check list.txt` | Who would be mailed, and who is left out and why |
| `mailsend send -a ACCOUNT -t MESSAGE list.txt` | Dry run: builds every mail and shows the first, sends nothing |
| `mailsend send -a ACCOUNT -t MESSAGE list.txt --live` | Really send (`--gap 45`, `--cooldown 30` to change the defaults) |
| `mailsend history` | The last mails sent |

`-` reads the list from the keyboard or a pipe, so the harvester's output can go straight in:

```bash
harvest export --days 1 | mailsend send -a me -t java-roles - --live
```

A message is one JSON file in `templates/`. [`examples/templates/`](examples/templates) has one to start from:

```bash
mkdir -p templates && cp examples/templates/job-application.json templates/
```

## How it works

```
 paste ─► pick out addresses ─► leave out: duplicates, mailed in the last N days,
                                refused before, no-reply, typos
                                        │
                                        ▼
 message + attachments ─► one mail per address ─► SMTP (app password)  ─┐
                                                   Gmail API (token.json) ─┼─► history (SQLite)
                               45 s gap · daily limit · 3 tries · Stop    ─┘
```

| File | Job |
|---|---|
| `mailsender/accounts.py` | Accounts, provider servers, the private accounts file |
| `mailsender/transport.py` | SMTP and Gmail API sending; sorts errors into "try again" / "this address" / "this account" |
| `mailsender/oauth.py` | Creates a send-only Gmail token (installed-app flow with PKCE) |
| `mailsender/recipients.py` | Picks addresses out of any text and says why each skipped one was skipped |
| `mailsender/message.py` | Messages, `{company}` / `{email}`, attachments |
| `mailsender/campaign.py` | The send loop: gap, daily limit, retries, Stop |
| `mailsender/history.py` | Who was mailed when, and which addresses were refused |
| `mailsender/ui.py`, `ui.html` | The screen |
| `mailsender/cli.py` | The `mailsend` command |

Standard library only: `smtplib` for SMTP, `urllib` for the Gmail API and Google sign-in, `sqlite3` for the history.

## Tests

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

The tests send real mail over SMTP to a small fake mail server in `tests/fakesmtp.py`, which can refuse a login, refuse an address, be busy, drop the connection or hit a daily cap. The Gmail API is tested with Google's answers faked. No test touches the internet.

## License

MIT
