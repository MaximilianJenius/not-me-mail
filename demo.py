"""Fake mailbox for trying the dashboard without connecting a real account (python app.py --demo)."""
from __future__ import annotations

DEMO_CONFIG = {
    "username": "coolname@hotmail.sg",
    "auth": "password",
    "my_addresses": ["coolname@hotmail.sg"],
    "my_names": ["Alex"],
    "known_service_domains": ["shopee.sg"],
    "scan_folder": "INBOX",
    "target_folder": "Not Me",
}


def _eml(frm, to, subject, body, date, extra=""):
    return (f"From: {frm}\r\nTo: {to}\r\nSubject: {subject}\r\nDate: {date}\r\n{extra}"
            f"MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}").encode()


UNSUB = "List-Unsubscribe: <https://example.com/unsub>\r\n"
SAMPLES = [
    _eml("Zalora <hello@mail.zalora.sg>", "Ahmad Bin Ali <coolname@hotmail.sg>",
         "Welcome to Zalora - verify your email", "Hi Ahmad,\nPlease verify your email.",
         "Mon, 19 Oct 2026 09:12:00 +0800", UNSUB),
    _eml("Klook <noreply@klook.com>", "coolname@hotmail.sg", "Priya, your booking is confirmed",
         "Dear Priya,\nYour booking #A1234 is confirmed.", "Sun, 18 Oct 2026 20:01:00 +0800", UNSUB),
    _eml("Shopee <info@mail.shopee.sg>", "Alex <coolname@hotmail.sg>", "Your order has shipped",
         "Hi Alex,\nYour parcel is on the way.", "Sun, 18 Oct 2026 11:30:00 +0800", UNSUB),
    _eml("Deals Weekly <news@dealsweekly.com>", "undisclosed-recipients:;", "50% off this weekend only",
         "Big sale!", "Sat, 17 Oct 2026 08:00:00 +0800", UNSUB),
    _eml("FitClub <team@fitclub.sg>", "coolname@hotmail.sg", "Thanks for signing up!",
         "Hello John!\nThanks for signing up to FitClub.", "Fri, 16 Oct 2026 18:45:00 +0800", UNSUB),
    _eml("Mum <mum@gmail.com>", "coolname@hotmail.sg", "dinner sunday?", "Hi Alex, coming for dinner?",
         "Fri, 16 Oct 2026 07:10:00 +0800"),
    _eml("Zalora <hello@mail.zalora.sg>", "Ahmad Bin Ali <coolname@hotmail.sg>", "Ahmad, new arrivals for you",
         "Hi Ahmad,\nCheck out what's new.", "Thu, 15 Oct 2026 12:00:00 +0800", UNSUB),
]


class DemoClient:
    store = {str(i + 1): raw for i, raw in enumerate(SAMPLES)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def fetch_recent(self, folder, days, limit):
        return "1", iter(list(self.store.items())[:limit])

    def move(self, folder, uids, target, expected_uidvalidity):
        for u in uids:
            self.store.pop(u, None)
        return f"[demo] Moved {len(uids)} message(s) to '{target}'."
