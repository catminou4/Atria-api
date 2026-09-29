from __future__ import annotations

import random
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from .config import PacingConfig
from .errors import TransientProvisioningError


PACING_SCHEMA = """
CREATE TABLE IF NOT EXISTS pacing (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    last_started REAL NOT NULL DEFAULT 0,
    day TEXT NOT NULL DEFAULT '',
    day_count INTEGER NOT NULL DEFAULT 0,
    challenge_failures INTEGER NOT NULL DEFAULT 0,
    blocked_until REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO pacing(singleton) VALUES(1);
"""


class SingleLanePacer:
    def __init__(
        self,
        state_path: str | Path,
        config: PacingConfig,
        *,
        clock: Callable[[], float] = time.time,
        sleeper: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ):
        self.state_path = Path(state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.clock = clock
        self.sleeper = sleeper
        self.uniform = uniform
        with sqlite3.connect(self.state_path) as connection:
            connection.executescript(PACING_SCHEMA)
        self.state_path.chmod(0o600)

    def acquire(self) -> None:
        while True:
            now = self.clock()
            today = datetime.fromtimestamp(now, UTC).date().isoformat()
            with sqlite3.connect(self.state_path, timeout=30, isolation_level=None) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT * FROM pacing WHERE singleton=1").fetchone()
                assert row is not None
                last_started, day, day_count, blocked_until = row[1], row[2], row[3], row[5]
                if day != today:
                    day_count = 0
                if day_count >= self.config.max_runs_per_day:
                    connection.commit()
                    raise TransientProvisioningError(
                        "daily_run_limit",
                        "configured maximum runs per UTC day has been reached",
                    )
                interval = self.uniform(
                    self.config.min_interval_seconds, self.config.max_interval_seconds
                )
                ready_at = max(blocked_until, last_started + interval if last_started else now)
                if ready_at <= now:
                    connection.execute(
                        "UPDATE pacing SET last_started=?,day=?,day_count=? WHERE singleton=1",
                        (now, today, day_count + 1),
                    )
                    connection.commit()
                    return
                connection.commit()
            self.sleeper(max(0.0, ready_at - now))

    def record_challenge_result(self, succeeded: bool) -> None:
        now = self.clock()
        with sqlite3.connect(self.state_path, timeout=30, isolation_level=None) as connection:
            connection.execute("BEGIN IMMEDIATE")
            failures = connection.execute(
                "SELECT challenge_failures FROM pacing WHERE singleton=1"
            ).fetchone()[0]
            if succeeded:
                connection.execute(
                    "UPDATE pacing SET challenge_failures=0,blocked_until=0 WHERE singleton=1"
                )
            else:
                failures += 1
                blocked_until = 0.0
                if failures >= self.config.challenge_failure_threshold:
                    exponent = failures - self.config.challenge_failure_threshold
                    blocked_until = now + self.config.challenge_backoff_seconds * (2**exponent)
                connection.execute(
                    "UPDATE pacing SET challenge_failures=?,blocked_until=MAX(blocked_until,?) WHERE singleton=1",
                    (failures, blocked_until),
                )
            connection.commit()


class NoopPacer:
    def acquire(self) -> None:
        return

    def record_challenge_result(self, succeeded: bool) -> None:
        return
