from __future__ import annotations

import tempfile
import threading
import unittest
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from atria_keys.challenge import FixtureChallengeDriver
from atria_keys.driver import FixtureRegistrationDriver
from atria_keys.keystore import KeyStore
from atria_keys.mailbox import FixtureMailboxReader
from atria_keys.pacing import NoopPacer
from atria_keys.pipeline import Pipeline
from atria_keys.state import StateStore


class PipelineTests(unittest.TestCase):
    def make_pipeline(
        self,
        root: Path,
        *,
        driver: FixtureRegistrationDriver | None = None,
        variant: str = "supported",
    ) -> tuple[Pipeline, StateStore, KeyStore, FixtureRegistrationDriver]:
        state = StateStore(root / "state.sqlite3")
        keystore = KeyStore(root / "keys.jsonl")
        fixture_driver = driver or FixtureRegistrationDriver()
        pipeline = Pipeline(
            state=state,
            mailbox=FixtureMailboxReader(),
            driver=fixture_driver,
            challenge=FixtureChallengeDriver(root / "artifacts", variant),
            keystore=keystore,
            pending_dir=root / ".pending",
            pacer=NoopPacer(),
            retry_bounds=(0, 0),
            sleeper=lambda _: None,
        )
        return pipeline, state, keystore, fixture_driver

    def test_all_stages_and_dedupe_on_completed_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pipeline, state, keystore, driver = self.make_pipeline(Path(directory))
            first = pipeline.run(run_id="run-one")
            second = pipeline.run(run_id="run-one")
            self.assertEqual(first.status, "completed")
            self.assertEqual(second.status, "completed")
            self.assertEqual(len(keystore.records()), 1)
            self.assertEqual(driver.register_calls, 1)
            run = state.get("run-one")
            assert run
            self.assertEqual(run.next_stage, 5)
            self.assertEqual(run.status, "completed")

    def test_resume_after_register_does_not_register_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            driver = FixtureRegistrationDriver()
            pipeline, state, keystore, driver = self.make_pipeline(root, driver=driver)
            state.create_or_get("resume-run", "saved@example.test")
            state.checkpoint(
                "resume-run",
                next_stage=2,
                data={"registration_submitted": "true"},
                email="saved@example.test",
            )
            result = pipeline.run(run_id="resume-run")
            self.assertEqual(result.status, "completed")
            self.assertEqual(driver.register_calls, 0)
            self.assertEqual(len(keystore.records()), 1)

    def test_unsupported_variant_is_fatal_and_emits_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, state, _, _ = self.make_pipeline(root, variant="rotated-v9")
            result = pipeline.run(run_id="bad-widget")
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "unsupported_challenge_variant")
            self.assertEqual(result.failure_class, "fatal")
            self.assertTrue(result.artifact)
            self.assertTrue(Path(result.artifact or "").exists())
            run = state.get("bad-widget")
            assert run
            self.assertEqual(run.next_stage, 2)

    def test_resume_persist_after_append_before_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pipeline, state, keystore, _ = self.make_pipeline(root)
            result = pipeline.run(run_id="persist-crash")
            self.assertEqual(result.status, "completed")
            # Rewind just the checkpoint to emulate a crash after fsync and pending deletion.
            run = state.get("persist-crash")
            assert run
            state.checkpoint(
                "persist-crash",
                next_stage=4,
                data=run.data,
                email=run.email,
                status="running",
            )
            resumed_driver = FixtureRegistrationDriver()
            resumed, _, _, _ = self.make_pipeline(root, driver=resumed_driver)
            resumed_result = resumed.run(run_id="persist-crash")
            self.assertEqual(resumed_result.status, "completed")
            self.assertEqual(len(keystore.records()), 1)
            self.assertEqual(resumed_driver.register_calls, 0)

    def test_pipeline_with_served_registration_and_widget_html(self) -> None:
        fixtures = Path(__file__).parent / "fixtures"

        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

        server = None
        try:
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0),
                lambda *args, **kwargs: QuietHandler(*args, directory=str(fixtures), **kwargs),
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_port}"
        except PermissionError:
            # Some hermetic CI sandboxes deny even loopback listeners; exercise the same
            # HTML fixture through urllib's file transport there.
            base_url = fixtures.as_uri()

        class FixturePage:
            def __init__(self, source: str):
                self.source = source

            def content(self) -> str:
                return self.source

        class ServedDriver(FixtureRegistrationDriver):
            def __init__(self, base_url: str):
                super().__init__()
                self.base_url = base_url

            def register(self, email: str) -> dict[str, str]:
                registration = urllib.request.urlopen(
                    f"{self.base_url}/registration.html", timeout=2
                ).read().decode()
                self.assert_fixture("type=\"email\"", registration)
                widget = urllib.request.urlopen(
                    f"{self.base_url}/widget_supported.html", timeout=2
                ).read().decode()
                self._page = FixturePage(widget)
                return super().register(email)

            @staticmethod
            def assert_fixture(needle: str, source: str) -> None:
                if needle not in source:
                    raise AssertionError(f"fixture missing {needle}")

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                driver = ServedDriver(base_url)
                state = StateStore(root / "state.sqlite3")
                keystore = KeyStore(root / "keys.jsonl")
                pipeline = Pipeline(
                    state=state,
                    mailbox=FixtureMailboxReader(),
                    driver=driver,
                    challenge=FixtureChallengeDriver(root / "artifacts", "auto"),
                    keystore=keystore,
                    pending_dir=root / ".pending",
                    pacer=NoopPacer(),
                    retry_bounds=(0, 0),
                    sleeper=lambda _: None,
                )
                result = pipeline.run(run_id="served-fixture")
                self.assertEqual(result.status, "completed")
                self.assertEqual(len(keystore.records()), 1)
        finally:
            if server is not None:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
