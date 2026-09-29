from __future__ import annotations

import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .challenge import ChallengeDriver
from .driver import RegistrationDriver
from .errors import FailureClass, FatalProvisioningError, ProvisioningError
from .keystore import KeyRecord, KeyStore
from .mailbox import MailboxReader
from .state import StateStore


LOGGER = logging.getLogger(__name__)
STAGES = ("mailbox", "register", "challenge", "key_extract", "persist")


@dataclass(slots=True)
class PipelineResult:
    run_id: str
    status: str
    email: str | None
    key_id: str | None = None
    error_code: str | None = None
    failure_class: str | None = None
    artifact: str | None = None


class Pipeline:
    def __init__(
        self,
        *,
        state: StateStore,
        mailbox: MailboxReader,
        driver: RegistrationDriver,
        challenge: ChallengeDriver,
        keystore: KeyStore,
        pending_dir: str | Path,
        pacer: Any,
        max_stage_attempts: int = 3,
        retry_bounds: tuple[float, float] = (2.0, 8.0),
        sleeper: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ):
        self.state = state
        self.mailbox = mailbox
        self.driver = driver
        self.challenge = challenge
        self.keystore = keystore
        self.pending_dir = Path(pending_dir)
        self.pacer = pacer
        self.max_stage_attempts = max_stage_attempts
        self.retry_bounds = retry_bounds
        self.sleeper = sleeper
        self.uniform = uniform

    def _write_pending(
        self, run_id: str, key_id: str, api_key: str, meta: dict[str, Any]
    ) -> Path:
        self.pending_dir.mkdir(parents=True, exist_ok=True)
        safe_run_id = "".join(c for c in run_id if c.isalnum() or c in "-_.")[:80]
        target = self.pending_dir / f"{safe_run_id}.json"
        temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        payload = json.dumps({"key_id": key_id, "api_key": api_key, "meta": meta}).encode()
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
        dir_fd = os.open(self.pending_dir, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return target

    @staticmethod
    def _read_pending(path: str | Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def _execute_stage(self, stage: str, run_id: str, email: str | None, data: dict[str, Any]) -> str | None:
        if stage == "mailbox":
            return email or self.mailbox.prepare_address(run_id)
        if stage == "register":
            if not email:
                raise FatalProvisioningError("email_missing", "mailbox stage produced no email", stage=stage)
            data.update(self.driver.register(email))
            return email
        if stage == "challenge":
            try:
                self.challenge.handle(self.driver.page, run_id)
            except Exception:
                self.pacer.record_challenge_result(False)
                raise
            self.pacer.record_challenge_result(True)
            self.driver.checkpoint_session()
            data["challenge_complete"] = True
            return email
        if stage == "key_extract":
            if not email:
                raise FatalProvisioningError("email_missing", "run email is unavailable", stage=stage)
            verification = self.mailbox.wait_for_verification(email)
            key_id, api_key, meta = self.driver.extract_key(verification)
            pending = self._write_pending(run_id, key_id, api_key, meta)
            data["pending_path"] = str(pending)
            data["key_id"] = key_id
            return email
        if stage == "persist":
            pending_path = data.get("pending_path")
            if not pending_path or not Path(pending_path).exists():
                # A crash after a successful append but before checkpoint is safe to finalize.
                existing = next((row for row in self.keystore.records() if row.run_id == run_id), None)
                if existing:
                    data["key_id"] = existing.key_id
                    return email
                raise FatalProvisioningError(
                    "pending_key_missing", "pending key material is unavailable", stage=stage
                )
            pending = self._read_pending(pending_path)
            record = KeyRecord.create(
                key_id=pending["key_id"],
                api_key=pending["api_key"],
                email=email or "",
                run_id=run_id,
                meta=pending.get("meta") or {},
            )
            self.keystore.append(record)
            Path(pending_path).unlink(missing_ok=True)
            return email
        raise AssertionError(f"unknown stage: {stage}")

    def run(self, *, run_id: str | None = None, email: str | None = None) -> PipelineResult:
        run_id = run_id or uuid.uuid4().hex
        run = self.state.create_or_get(run_id, email)
        if run.status == "completed":
            return PipelineResult(run_id, "completed", run.email, run.data.get("key_id"))
        email = run.email or email
        data = dict(run.data)
        self.state.checkpoint(
            run_id, next_stage=run.next_stage, data=data, email=email, status="running"
        )
        try:
            self.driver.begin(run_id)
            for stage_index in range(run.next_stage, len(STAGES)):
                stage = STAGES[stage_index]
                for attempt in range(1, self.max_stage_attempts + 1):
                    started = time.monotonic()
                    try:
                        email = self._execute_stage(stage, run_id, email, data)
                        elapsed = int((time.monotonic() - started) * 1000)
                        self.state.record_event(
                            run_id, stage, "success", attempt=attempt, elapsed_ms=elapsed
                        )
                        status = "completed" if stage_index + 1 == len(STAGES) else "running"
                        self.state.checkpoint(
                            run_id,
                            next_stage=stage_index + 1,
                            data=data,
                            email=email,
                            status=status,
                        )
                        LOGGER.info("run=%s stage=%s outcome=success", run_id, stage)
                        break
                    except ProvisioningError as exc:
                        elapsed = int((time.monotonic() - started) * 1000)
                        self.state.record_event(
                            run_id,
                            stage,
                            "failure",
                            failure_class=exc.failure_class.value,
                            reason=exc.code,
                            attempt=attempt,
                            elapsed_ms=elapsed,
                        )
                        retry = (
                            exc.failure_class == FailureClass.TRANSIENT
                            and attempt < self.max_stage_attempts
                        )
                        LOGGER.warning(
                            "run=%s stage=%s outcome=failure class=%s reason=%s retry=%s",
                            run_id,
                            stage,
                            exc.failure_class.value,
                            exc.code,
                            retry,
                        )
                        if retry:
                            self.sleeper(self.uniform(*self.retry_bounds))
                            continue
                        self.state.fail(run_id, exc.code, exc.failure_class.value)
                        return PipelineResult(
                            run_id,
                            "failed",
                            email,
                            data.get("key_id"),
                            exc.code,
                            exc.failure_class.value,
                            exc.artifact,
                        )
                else:  # pragma: no cover - loop exits through success or returned failure
                    raise AssertionError("stage attempt loop exhausted")
            return PipelineResult(run_id, "completed", email, data.get("key_id"))
        except ProvisioningError as exc:
            self.state.fail(run_id, exc.code, exc.failure_class.value)
            return PipelineResult(
                run_id,
                "failed",
                email,
                data.get("key_id"),
                exc.code,
                exc.failure_class.value,
                exc.artifact,
            )
        except Exception:
            LOGGER.exception("run=%s failed outside a stage", run_id)
            self.state.fail(run_id, "internal_error", FailureClass.FATAL.value)
            return PipelineResult(
                run_id, "failed", email, data.get("key_id"), "internal_error", "fatal"
            )
        finally:
            self.driver.close()

