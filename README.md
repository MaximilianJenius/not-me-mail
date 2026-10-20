# Not Me Mail

A local dashboard that scans your inbox over IMAP and flags ads, sign-ups and
orders that were meant for **someone else using your email address**, which is common
when your address is easy to remember. You can then move the ones you pick into a `Not Me` folder.

It runs only on your computer (`127.0.0.1`). Your mail is never sent anywhere else.

**Live demo:** https://maximilianjenius.github.io/not-me-mail/. It uses made-up emails and never connects to a real mailbox.

## How an email gets flagged

| Signal | Points | Example |
|---|---|---|
| Wrong name | 3 | `To: "Ahmad Bin Ali" <you@…>`, subject `Priya, your booking…`, body `Hi John,` |
| Sign-up / order from a service you don't use | 3 | "Welcome to…", "verify your email", "order confirmation" from a domain not in `known_service_domains` |
| Your address isn't in To/Cc | 1 | Arrived by BCC or through a mailing list |
| Bulk / marketing headers | 1 | `List-Unsubscribe`, `Precedence: bulk`, Mailchimp/SendGrid/… fingerprints |

**3+ points = likely not you**, **2 = possibly**. Click **It's mine** next to a sender
to add it to your known services. Its sign-up and order emails then stop being flagged.

## Try it first (no account needed)

```bash
cd not-me-mail
py -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py --demo
```

Then open http://127.0.0.1:5055.

## Connect your real mailbox

Copy `config.example.json` to `config.json` and fill in:

- `my_addresses`: your address(es), including any aliases
- `my_names`: your first name and any nicknames. The wrong-name check needs these.
- `known_service_domains`: shops and apps you really use, e.g. `shopee.sg`

### Outlook / Hotmail (`auth: "outlook_oauth"`)

Microsoft no longer accepts passwords over IMAP for Outlook.com, so you need a
free app registration (one-time, about 5 minutes):

1. Go to https://entra.microsoft.com, then **App registrations**, then **New registration**.
   (If it asks you to create a directory or a free Azure account, do that first.)
   - Name: `Not Me Mail`
   - Supported account types: **Personal Microsoft accounts only**
   - Redirect URI: leave empty
2. In the new app, open **Authentication**, scroll to **Allow public client flows** and set it to **Yes**. Save.
3. Copy the **Application (client) ID** into `outlook_client_id` in `config.json`.
4. Run `.venv\Scripts\python app.py`, click **Sign in with Microsoft**, and enter the code shown.
   The app requests only `IMAP.AccessAsUser.All`. The sign-in token is saved in `.token_cache.json`,
   which stays on your computer and is git-ignored.

Keep `imap_host` as `outlook.office365.com`.

### Gmail, Yahoo, others (`auth: "password"`)

Set `imap_host` (Gmail: `imap.gmail.com`, Yahoo: `imap.mail.yahoo.com`) and create an
**app password** in your account's security settings (requires 2-step verification). Then:

```powershell
$env:IMAP_PASSWORD = "your-app-password"; .venv\Scripts\python app.py
```

## Safety notes

- Scanning uses read-only mode and `BODY.PEEK`, so nothing is marked as read.
- Moving re-checks the mailbox's `UIDVALIDITY` first, so it won't move the wrong emails if the server renumbered them.
  It uses IMAP `MOVE` (or `UID EXPUNGE`), which affects only the selected emails.
- Unsubscribe links are shown, never clicked. Clicking one tells the sender your address
  is real, so only use them for senders you trust.

## Updating the live demo

The demo page in `docs/` is built from `static/index.html`, with the sample emails from `demo.py`
scored ahead of time. After changing either file, or `detector.py`, rebuild and commit:

```bash
.venv\Scripts\python build_pages.py
```

GitHub Pages serves the `docs/` folder of the `main` branch.

## Tests

```bash
.venv\Scripts\python -m unittest test_detector
```
