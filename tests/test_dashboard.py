from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from atria_keys.dashboard import Dashboard
from atria_keys.keystore import KeyRecord, KeyStore
from atria_keys.state import StateStore


class DashboardTests(unittest.TestCase):
    def test_snapshot_masks_key_material(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = StateStore(root / "state.sqlite3")
            store = KeyStore(root / "keys.jsonl")
            secret = "atria-super-secret-value"
            store.append(
                KeyRecord.create(
                    key_id="kid", api_key=secret, email="a@example.test", run_id="run"
                )
            )
            snapshot = Dashboard(state, store).snapshot()
            encoded = json.dumps(snapshot)
            self.assertNotIn(secret, encoded)
            self.assertEqual(snapshot["key_count"], 1)
            self.assertIn("…", snapshot["keys"][0]["api_key"])


if __name__ == "__main__":
    unittest.main()

