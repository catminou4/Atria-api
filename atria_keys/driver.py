from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from .errors import FatalProvisioningError, TransientProvisioningError
from .mailbox import Verification


LOGGER = logging.getLogger(__name__)


class RegistrationDriver(Protocol):
    @property
    def page(self) -> Any: ...

    def begin(self, run_id: str) -> None: ...

    def register(self, email: str) -> dict[str, str]: ...

    def extract_key(self, verification: Verification) -> tuple[str, str, dict[str, Any]]: ...

    def checkpoint_session(self) -> None: ...

    def close(self) -> None: ...


class AtriaPlaywrightDriver:
    EMAIL_SELECTORS = (
        "input[type='email']",
        "input[name='email']",
        "input[autocomplete='email']",
    )
    SUBMIT_SELECTORS = (
        "button[type='submit']",
        "button:has-text('Continue')",
        "button:has-text('Register')",
        "button:has-text('Sign up')",
    )
    KEY_SELECTORS = (
        "[data-testid='api-key']",
        "code.api-key",
        "input[readonly][name*='key' i]",
    )

    def __init__(
        self,
        registration_url: str,
        session_dir: str | Path,
        artifact_dir: str | Path,
        *,
        headed: bool = True,
    ):
        self.registration_url = registration_url
        self.session_dir = Path(session_dir)
        self.artifact_dir = Path(artifact_dir)
        self.headed = headed
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._session_path: Path | None = None

    @property
    def page(self) -> Any:
        if self._page is None:
            raise RuntimeError("driver has not been started")
        return self._page

    def begin(self, run_id: str) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FatalProvisioningError(
                "playwright_missing",
                "install project dependencies and run `playwright install chromium`",
            ) from exc
        self.session_dir.mkdir(parents=True, exist_ok=True)
        safe_run_id = re.sub(r"[^A-Za-z0-9_.-]", "_", run_id)[:80]
        self._session_path = self.session_dir / f"{safe_run_id}.json"
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=not self.headed)
        context_options: dict[str, Any] = {}
        if self._session_path.exists():
            context_options["storage_state"] = str(self._session_path)
        self._context = self._browser.new_context(**context_options)
        self._page = self._context.new_page()

    def _snapshot(self, reason: str) -> str:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        target = self.artifact_dir / f"driver-{reason}-{os.getpid()}.html"
        target.write_text(self.page.content(), encoding="utf-8")
        os.chmod(target, 0o600)
        return str(target)

    @staticmethod
    def _first(page: Any, selectors: tuple[str, ...]) -> Any | None:
        for selector in selectors:
            locator = page.locator(selector)
            if locator.count():
                return locator.first
        return None

    def register(self, email: str) -> dict[str, str]:
        try:
            self.page.goto(self.registration_url, wait_until="domcontentloaded")
        except Exception as exc:
            raise TransientProvisioningError(
                "registration_navigation_failed", "registration page could not be loaded", stage="register"
            ) from exc

        host = (urlparse(self.page.url).hostname or "").lower()
        google_button = self.page.locator("text=/sign in with google/i")
        if host.startswith("auth.") or google_button.count():
            artifact = self._snapshot("unsupported-auth-flow")
            raise FatalProvisioningError(
                "unsupported_auth_flow",
                "registration redirected to OIDC/Google; configure the provider-issued partnership URL",
                stage="register",
                artifact=artifact,
            )

        email_input = self._first(self.page, self.EMAIL_SELECTORS)
        submit = self._first(self.page, self.SUBMIT_SELECTORS)
        if email_input is None or submit is None:
            artifact = self._snapshot("registration-selector")
            raise FatalProvisioningError(
                "dead_selector",
                "registration email field or submit control was not found",
                stage="register",
                artifact=artifact,
            )
        email_input.fill(email)
        submit.click()
        self.checkpoint_session()
        return {"registration_submitted": "true"}

    def extract_key(self, verification: Verification) -> tuple[str, str, dict[str, Any]]:
        try:
            if verification.link:
                self.page.goto(verification.link, wait_until="domcontentloaded")
            elif verification.otp:
                otp = self._first(
                    self.page, ("input[autocomplete='one-time-code']", "input[name*='otp' i]")
                )
                if otp is None:
                    raise FatalProvisioningError(
                        "dead_selector", "OTP input was not found", stage="key_extract"
                    )
                otp.fill(verification.otp)
                submit = self._first(self.page, self.SUBMIT_SELECTORS)
                if submit is None:
                    raise FatalProvisioningError(
                        "dead_selector", "OTP submit control was not found", stage="key_extract"
                    )
                submit.click()

            key_node = self._first(self.page, self.KEY_SELECTORS)
            if key_node is None:
                create = self.page.locator("button:has-text('Create API key')")
                if create.count():
                    create.first.click()
                    key_node = self._first(self.page, self.KEY_SELECTORS)
            if key_node is None:
                artifact = self._snapshot("key-selector")
                raise FatalProvisioningError(
                    "dead_selector",
                    "API key display was not found after verification",
                    stage="key_extract",
                    artifact=artifact,
                )
            api_key = key_node.input_value() if key_node.evaluate("e => e.tagName === 'INPUT'") else key_node.inner_text()
            api_key = api_key.strip()
            if not api_key:
                raise TransientProvisioningError(
                    "empty_api_key", "API key control was empty", stage="key_extract"
                )
            key_id = hashlib.sha256(api_key.encode()).hexdigest()[:16]
            self.checkpoint_session()
            return key_id, api_key, {"source": "atria-console"}
        except (FatalProvisioningError, TransientProvisioningError):
            raise
        except Exception as exc:
            raise TransientProvisioningError(
                "key_extraction_failed", "could not complete verification/key extraction", stage="key_extract"
            ) from exc

    def checkpoint_session(self) -> None:
        if self._context is not None and self._session_path is not None:
            self._context.storage_state(path=str(self._session_path))
            os.chmod(self._session_path, 0o600)

    def close(self) -> None:
        if self._context is not None:
            self.checkpoint_session()
            self._context.close()
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._context = self._browser = self._playwright = self._page = None


class FixtureRegistrationDriver:
    def __init__(self):
        self.run_id = ""
        self.register_calls = 0
        self.extract_calls = 0
        self._page = object()

    @property
    def page(self) -> Any:
        return self._page

    def begin(self, run_id: str) -> None:
        self.run_id = run_id

    def register(self, email: str) -> dict[str, str]:
        self.register_calls += 1
        return {"registration_submitted": "true"}

    def extract_key(self, verification: Verification) -> tuple[str, str, dict[str, Any]]:
        self.extract_calls += 1
        digest = hashlib.sha256(self.run_id.encode()).hexdigest()
        return digest[:16], f"fixture-{digest}", {"fixture": True}

    def checkpoint_session(self) -> None:
        return

    def close(self) -> None:
        return
