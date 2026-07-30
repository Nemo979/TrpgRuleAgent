import unittest

from trpg_app.auth import SessionSigner, password_matches


class SessionSignerTest(unittest.TestCase):
    def test_issues_and_verifies_bounded_session(self) -> None:
        signer = SessionSigner("secret", ttl_seconds=60)
        token = signer.issue(now=100)

        self.assertTrue(signer.verify(token, now=159))
        self.assertFalse(signer.verify(token, now=161))
        self.assertFalse(signer.verify(token + "x", now=120))

    def test_password_comparison(self) -> None:
        self.assertTrue(password_matches("shared", "shared"))
        self.assertFalse(password_matches("wrong", "shared"))


if __name__ == "__main__":
    unittest.main()
