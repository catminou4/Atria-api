from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    email TEXT,
    next_stage INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    data_json TEXT NOT NULL DEFAULT '{}',
    last_error_code TEXT,
    last_error_class TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    outcome TEXT NOT NULL,
    failure_class TEXT,
    reason TEXT,
    attempt INTEGER NOT NULL DEFAULT 1,
    elapsed_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_run_id ON events(run_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class RunState:
    run_id: str
    email: str | None
    next_stage: int
    status: str
    data: dict[str, Any]
    last_error_code: str | None
    last_error_class: str | None


class StateStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self.connect() as connection:
            connection.executescript(SCHEMA)
        self.path.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def create_or_get(self, run_id: str, email: str | None = None) -> RunState:
        now = _now()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO runs(run_id,email,created_at,updated_at) VALUES(?,?,?,?)",
                (run_id, email, now, now),
            )
            if email:
                connection.execute(
                    "UPDATE runs SET email=COALESCE(email,?), updated_at=? WHERE run_id=?",
                    (email, now, run_id),
                )
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        assert row is not None
        return self._decode(row)

    @staticmethod
    def _decode(row: sqlite3.Row) -> RunState:
        return RunState(
            run_id=row["run_id"],
            email=row["email"],
            next_stage=row["next_stage"],
            status=row["status"],
            data=json.loads(row["data_json"]),
            last_error_code=row["last_error_code"],
            last_error_class=row["last_error_class"],
        )

    def get(self, run_id: str) -> RunState | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return self._decode(row) if row else None

    def checkpoint(
        self,
        run_id: str,
        *,
        next_stage: int,
        data: dict[str, Any],
        email: str | None = None,
        status: str = "running",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE runs SET next_stage=?, data_json=?, email=COALESCE(?,email),
                   status=?, last_error_code=NULL, last_error_class=NULL, updated_at=? WHERE run_id=?""",
                (next_stage, json.dumps(data, sort_keys=True), email, status, _now(), run_id),
            )

    def fail(self, run_id: str, code: str, failure_class: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE runs SET status='failed', last_error_code=?, last_error_class=?, updated_at=? WHERE run_id=?",
                (code, failure_class, _now(), run_id),
            )

    def record_event(
        self,
        run_id: str,
        stage: str,
        outcome: str,
        *,
        failure_class: str | None = None,
        reason: str | None = None,
        attempt: int = 1,
        elapsed_ms: int = 0,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO events(run_id,stage,outcome,failure_class,reason,attempt,elapsed_ms,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (run_id, stage, outcome, failure_class, reason, attempt, elapsed_ms, _now()),
            )

    def incomplete_runs(self) -> list[RunState]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE status IN ('pending','running') ORDER BY created_at"
            ).fetchall()
        return [self._decode(row) for row in rows]

    def dashboard_snapshot(self) -> dict[str, Any]:
        with self.connect() as connection:
            in_flight = connection.execute(
                "SELECT COUNT(*) FROM runs WHERE status IN ('pending','running')"
            ).fetchone()[0]
            run_counts = {
                row["status"]: row["count"]
                for row in connection.execute(
                    "SELECT status, COUNT(*) AS count FROM runs GROUP BY status"
                )
            }
            stages = [
                dict(row)
                for row in connection.execute(
                    """SELECT stage,outcome,COUNT(*) AS count FROM events
                       GROUP BY stage,outcome ORDER BY stage,outcome"""
                )
            ]
            reasons = [
                dict(row)
                for row in connection.execute(
                    """SELECT reason,COUNT(*) AS count FROM events WHERE outcome='failure'
                       GROUP BY reason ORDER BY count DESC"""
                )
            ]
            challenge_total = connection.execute(
                "SELECT COUNT(DISTINCT run_id) FROM events WHERE stage='challenge'"
            ).fetchone()[0]
            challenge_first = connection.execute(
                """SELECT COUNT(*) FROM events WHERE stage='challenge' AND outcome='success'
                   AND attempt=1"""
            ).fetchone()[0]
        return {
            "in_flight": in_flight,
            "runs": run_counts,
            "stages": stages,
            "failure_reasons": reasons,
            "challenge_first_pass_rate": (
                challenge_first / challenge_total if challenge_total else None
            ),
        }
