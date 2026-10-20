import unittest

from detector import Profile, analyze, find_greeting_names

PROFILE = Profile(
    my_addresses=["coolname@hotmail.sg"],
    my_names=["Alex Tan"],
    known_service_domains=["shopee.sg", "grab.com"],
)


def eml(headers: dict, body: str, html: bool = False) -> bytes:
    lines = [f"{k}: {v}" for k, v in headers.items()]
    lines.append("MIME-Version: 1.0")
    lines.append(f"Content-Type: text/{'html' if html else 'plain'}; charset=utf-8")
    return ("\r\n".join(lines) + "\r\n\r\n" + body).encode()


class GreetingTests(unittest.TestCase):
    def test_extracts_names(self):
        self.assertEqual(find_greeting_names("Hi Ahmad,\nthanks"), ["Ahmad"])
        self.assertEqual(find_greeting_names("Dear Mary Tan,\n"), ["Mary"])
        self.assertEqual(find_greeting_names("HELLO John!"), ["John"])

    def test_ignores_generic(self):
        self.assertEqual(find_greeting_names("Dear Valued Customer,\n"), [])
        self.assertEqual(find_greeting_names("Hi there,\n"), [])
        self.assertEqual(find_greeting_names("Welcome to Shopee!"), [])


class AnalyzeTests(unittest.TestCase):
    def test_someone_elses_signup_is_likely(self):
        raw = eml({
            "From": "Zalora <hello@mail.zalora.sg>",
            "To": "Ahmad Bin Ali <coolname@hotmail.sg>",
            "Subject": "Welcome to Zalora - verify your email",
            "List-Unsubscribe": "<https://zalora.sg/unsub?x=1>",
        }, "Hi Ahmad,\nPlease verify your email.")
        v = analyze("1", raw, PROFILE)
        self.assertEqual(v.level, "likely")
        self.assertIn("Ahmad", v.names_seen)
        self.assertEqual(v.sender_domain, "zalora.sg")
        self.assertEqual(v.unsubscribe, "https://zalora.sg/unsub?x=1")

    def test_my_own_mail_is_clean(self):
        raw = eml({
            "From": "Shopee <info@mail.shopee.sg>",
            "To": "Alex <coolname@hotmail.sg>",
            "Subject": "Your order has shipped",
        }, "Hi Alex,\nYour parcel is on the way.")
        v = analyze("2", raw, PROFILE)
        self.assertEqual(v.level, "clean", v.reasons)

    def test_bcc_newsletter_is_possible(self):
        raw = eml({
            "From": "Deals <news@deals.example.com>",
            "To": "undisclosed-recipients:;",
            "Subject": "50% off this weekend",
            "List-Unsubscribe": "<mailto:unsub@deals.example.com>",
        }, "Big sale!")
        v = analyze("3", raw, PROFILE)
        self.assertEqual(v.level, "possible", v.reasons)

    def test_plus_address_counts_as_me(self):
        raw = eml({
            "From": "a@b.com", "To": "coolname+shop@hotmail.sg", "Subject": "hello",
        }, "nothing")
        self.assertEqual(analyze("4", raw, PROFILE).score, 0)

    def test_html_greeting_and_subject_name(self):
        raw = eml({
            "From": "Bank <alerts@somebank.com>",
            "To": "coolname@hotmail.sg",
            "Subject": "Priya, your statement is ready",
        }, "<html><body><p>Dear Priya,</p><p>See attached.</p></body></html>", html=True)
        v = analyze("5", raw, PROFILE)
        self.assertEqual(v.level, "likely")
        self.assertEqual(v.names_seen, ["Priya"])


if __name__ == "__main__":
    unittest.main()
