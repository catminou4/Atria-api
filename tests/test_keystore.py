from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from atria_keys.keystore import KeyRecord, KeyStore


class KeyStoreTests(unittest.TestCase):
    def test_atomic_concurrent_append_and_dedupe(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "keys.jsonl"
            store = KeyStore(path)
            records = [
                KeyRecord.create(
                    key_id=f"id-{index}",
                    api_key=f"fixture-secret-{index}",
                    email=f"user-{index}@example.test",
                    run_id=f"run-{index}",
                )
                for index in range(30)
            ]
            threads = [threading.Thread(target=store.append, args=(record,)) for record in records]
            threads += [threading.Thread(target=store.append, args=(records[0],)) for _ in range(8)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(rows), 30)
            self.assertEqual(len({row["api_key"] for row in rows}), 30)
            self.assertFalse(store.append(records[0]))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = KeyStore(root / "keys.jsonl")
            store.append(KeyRecord.create(key_id="id", api_key="secret", email="a@b", run_id="r"))
            txt = store.export(root / "keys.txt", "txt")
            env = store.export(root / "keys.env", "env")
            self.assertEqual(txt.read_text(), "secret\n")
            self.assertEqual(env.read_text(), "ATRIA_API_KEY_1=secret\n")


if __name__ == "__main__":
    unittest.main()

