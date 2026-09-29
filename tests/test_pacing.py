from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from atria_keys.config import PacingConfig
from atria_keys.pacing import SingleLanePacer


class FakeTime:
    def __init__(self) -> None:
        self.value = 1_700_000_000.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


class PacingTests(unittest.TestCase):
    def test_interval_and_challenge_backoff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fake = FakeTime()
            config = PacingConfig(
                min_interval_seconds=10,
                max_interval_seconds=10,
                max_runs_per_day=10,
                challenge_failure_threshold=2,
                challenge_backoff_seconds=30,
            )
            pacer = SingleLanePacer(
                Path(directory) / "state.sqlite3",
                config,
                clock=fake.now,
                sleeper=fake.sleep,
                uniform=lambda low, high: low,
            )
            pacer.acquire()
            pacer.acquire()
            self.assertEqual(fake.sleeps, [10])
            pacer.record_challenge_result(False)
            pacer.record_challenge_result(False)
            pacer.acquire()
            self.assertEqual(fake.sleeps[-1], 30)
            pacer.record_challenge_result(True)


if __name__ == "__main__":
    unittest.main()
