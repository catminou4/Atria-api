from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class MailboxConfig:
    kind: str = "imap_catchall"
    host: str = ""
    port: int = 993
    username: str = ""
    password_env: str = "ATRIA_MAILBOX_PASSWORD"
    use_ssl: bool = True
    recipient_domain: str = "example.invalid"
    poll_seconds: float = 5.0
    timeout_seconds: float = 300.0


@dataclass(slots=True)
class PacingConfig:
    min_interval_seconds: float = 600.0
    max_interval_seconds: float = 1200.0
    max_runs_per_day: int = 50
    challenge_failure_threshold: int = 3
    challenge_backoff_seconds: float = 1800.0


@dataclass(slots=True)
class AppConfig:
    mailbox: MailboxConfig = field(default_factory=MailboxConfig)
    pacing: PacingConfig = field(default_factory=PacingConfig)
    dashboard_port: int = 8686
    keystore_path: Path = Path("keys/keys.jsonl")
    state_path: Path = Path("keys/state.sqlite3")
    session_dir: Path = Path("keys/sessions")
    artifact_dir: Path = Path("keys/artifacts")
    pending_dir: Path = Path("keys/.pending")
    headed: bool = True
    challenge_timeout_seconds: float = 300.0
    registration_url: str = "https://api.atria-asi.ai/console"


def _coerce_path(value: Any, base: Path) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - dependency installed by package
        raise RuntimeError("PyYAML is required; install the project with `pip install -e .`") from exc
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    mailbox = MailboxConfig(**(raw.get("mailbox") or {}))
    pacing = PacingConfig(**(raw.get("pacing") or {}))
    root = config_path.parent.parent
    return AppConfig(
        mailbox=mailbox,
        pacing=pacing,
        dashboard_port=int(raw.get("dashboard_port", 8686)),
        keystore_path=_coerce_path(raw.get("keystore_path", "keys/keys.jsonl"), root),
        state_path=_coerce_path(raw.get("state_path", "keys/state.sqlite3"), root),
        session_dir=_coerce_path(raw.get("session_dir", "keys/sessions"), root),
        artifact_dir=_coerce_path(raw.get("artifact_dir", "keys/artifacts"), root),
        pending_dir=_coerce_path(raw.get("pending_dir", "keys/.pending"), root),
        headed=bool(raw.get("headed", True)),
        challenge_timeout_seconds=float(raw.get("challenge_timeout_seconds", 300)),
        registration_url=str(raw.get("registration_url", "https://api.atria-asi.ai/console")),
    )
