from __future__ import annotations

import html
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Protocol

from .errors import FatalProvisioningError, TransientProvisioningError


LOGGER = logging.getLogger(__name__)


class ChallengeDriver(Protocol):
    def handle(self, page: Any, run_id: str) -> None: ...


def _safe_name(run_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)[:80]


class AlibabaCloudChallengeDriver:
    """Preserve the genuine widget and await its provider-issued completion signal.

    This driver intentionally does not synthesize pointer gestures or forge challenge
    tokens. In headed mode a local operator completes the in-page challenge; the driver
    observes the widget's own result and persists the browser session.
    """

    IFRAME_SELECTOR = (
        "iframe[src*='alibabacloud'], iframe[src*='aliyun'], "
        "iframe[src*='captcha'], iframe[title*='challenge' i]"
    )
    TOKEN_SELECTORS = (
        "input[name*='captcha' i]",
        "input[name*='challenge' i]",
        "input[name*='verification' i]",
    )

    def __init__(self, artifact_dir: str | Path, timeout_seconds: float = 300):
        self.artifact_dir = Path(artifact_dir)
        self.timeout_seconds = timeout_seconds

    def _snapshot(self, page: Any, run_id: str, reason: str) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifact_dir / f"{_safe_name(run_id)}-{reason}.html"
        source = page.content()
        # Do not persist values commonly carried by credential inputs.
        source = re.sub(
            r'(<input[^>]+(?:name|type)=["\'](?:password|api[_-]?key)["\'][^>]*value=["\'])[^"\']*',
            r"\1[REDACTED]",
            source,
            flags=re.IGNORECASE,
        )
        temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, source.encode("utf-8", "replace"))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp, path)
        return path

    def handle(self, page: Any, run_id: str) -> None:
        iframe = page.locator(self.IFRAME_SELECTOR)
        if iframe.count() == 0:
            token_present = any(page.locator(selector).count() for selector in self.TOKEN_SELECTORS)
            if token_present:
                return
            artifact = self._snapshot(page, run_id, "unsupported-challenge")
            raise FatalProvisioningError(
                "unsupported_challenge_variant",
                "no recognized challenge iframe or completion field was found",
                stage="challenge",
                artifact=str(artifact),
            )

        source = (iframe.first.get_attribute("src") or "").lower()
        if not any(provider in source for provider in ("alibabacloud", "aliyun", "captcha")):
            artifact = self._snapshot(page, run_id, "unsupported-challenge")
            raise FatalProvisioningError(
                "unsupported_challenge_variant",
                "challenge iframe provider is not recognized",
                stage="challenge",
                artifact=str(artifact),
            )

        LOGGER.info("challenge awaiting local completion in the visible browser")
        selectors_json = list(self.TOKEN_SELECTORS)
        try:
            page.wait_for_function(
                """selectors => selectors.some(selector =>
                    Array.from(document.querySelectorAll(selector)).some(node => node.value))""",
                selectors_json,
                timeout=self.timeout_seconds * 1000,
            )
        except Exception as exc:
            raise TransientProvisioningError(
                "challenge_completion_timeout",
                "the genuine widget did not report completion before timeout",
                stage="challenge",
            ) from exc


class FixtureChallengeDriver:
    def __init__(self, artifact_dir: str | Path, variant: str = "supported"):
        self.artifact_dir = Path(artifact_dir)
        self.variant = variant
        self.calls = 0

    def handle(self, page: Any, run_id: str) -> None:
        self.calls += 1
        variant = self.variant
        if variant == "auto":
            source = page.content()
            marker = re.search(r'data-fixture-challenge=["\']([^"\']+)', source)
            variant = marker.group(1) if marker else "missing"
        if variant == "supported":
            return
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = self.artifact_dir / f"{_safe_name(run_id)}-unsupported-challenge.html"
        artifact.write_text(
            f"<!doctype html><title>fixture</title><p>{html.escape(variant)}</p>",
            encoding="utf-8",
        )
        os.chmod(artifact, 0o600)
        raise FatalProvisioningError(
            "unsupported_challenge_variant",
            f"fixture challenge variant {variant!r} is unsupported",
            stage="challenge",
            artifact=str(artifact),
        )
