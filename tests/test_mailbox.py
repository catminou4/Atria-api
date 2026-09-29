from __future__ import annotations

import email
import unittest

from atria_keys.mailbox import extract_verification


class MailboxTests(unittest.TestCase):
    def test_exact_recipient_and_extracts_link_and_otp(self) -> None:
        message = email.message_from_string(
            "To: Atria <atria-run@example.test>\n"
            "Subject: Verify\nContent-Type: text/plain; charset=utf-8\n\n"
            "Confirm at https://example.test/verify?id=abc using 123456"
        )
        result = extract_verification(message, "atria-run@example.test")
        self.assertIsNotNone(result)
        assert result
        self.assertEqual(result.link, "https://example.test/verify?id=abc")
        self.assertEqual(result.otp, "123456")
        self.assertIsNone(extract_verification(message, "other@example.test"))


if __name__ == "__main__":
    unittest.main()

