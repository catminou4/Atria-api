from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

import fcntl

from .redaction import mask_secret


@dataclass(frozen=True, slots=True)
class KeyRecord:
    key_id: str
    api_key: str
    email: str
    created_at: str
    run_id: str
    meta: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        key_id: str,
        api_key: str,
        email: str,
        run_id: str,
        meta: dict[str, Any] | None = None,
    ) -> "KeyRecord":
        return cls(key_id, api_key, email, datetime.now(UTC).isoformat(), run_id, meta or {})


def key_hash(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


class KeyStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_unlocked(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    records.append(json.loads(line))
        return records

    def append(self, record: KeyRecord) -> bool:
        """Append one complete fsynced line. Return False when the key is a duplicate."""
        digest = key_hash(record.api_key)
        with self._locked():
            if any(key_hash(str(row["api_key"])) == digest for row in self._read_unlocked()):
                return False
            data = (json.dumps(asdict(record), separators=(",", ":"), sort_keys=True) + "\n").encode()
            fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        return True

    def records(self) -> list[KeyRecord]:
        with self._locked():
            return [KeyRecord(**row) for row in self._read_unlocked()]

    def masked_records(self) -> list[dict[str, Any]]:
        return [
            {
                "key_id": row.key_id,
                "api_key": mask_secret(row.api_key),
                "email": row.email,
                "created_at": row.created_at,
                "run_id": row.run_id,
                "meta": row.meta,
            }
            for row in self.records()
        ]

    def export(self, output: str | Path, fmt: str) -> Path:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        records = self.records()
        if fmt == "txt":
            content = "".join(f"{row.api_key}\n" for row in records)
        elif fmt == "env":
            content = "".join(
                f"ATRIA_API_KEY_{index}={row.api_key}\n" for index, row in enumerate(records, 1)
            )
        else:
            raise ValueError("format must be 'txt' or 'env'")
        temp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, content.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, target)
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return target
