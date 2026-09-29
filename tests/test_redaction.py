from __future__ import annotations

import logging
import unittest

from atria_keys.redaction import SecretRedactionFilter


class RedactionTests(unittest.TestCase):
    def test_formats_arguments_and_redacts_secret_shape(self) -> None:
        record = logging.LogRecord(
            "test",
            logging.INFO,
            __file__,
            1,
            "run=%s api_key=%s",
            ("abc", "atria-secretvalue123"),
            None,
        )
        SecretRedactionFilter().filter(record)
        rendered = record.getMessage()
        self.assertIn("run=abc", rendered)
        self.assertNotIn("secretvalue", rendered)
        self.assertIn("[REDACTED]", rendered)


if __name__ == "__main__":
    unittest.main()
