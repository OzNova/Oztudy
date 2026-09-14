"""SQLite concurrency stress test (stdlib only).

Hammers the store with parallel readers/writers through the public
``_load_doc``/``_save_doc``/``query_history_range`` API to prove the
LOCK + WAL + busy-timeout model holds: no ``database is locked`` errors
may escape, and the final document must still load.

Run::

    python3 -m unittest Planner.tests.test_concurrency -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.prev_data_dir = os.environ.get("OZTUDY_DATA_DIR")
        os.environ["OZTUDY_DATA_DIR"] = self.tmp.name
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)
        self.storage = storage_mod
        # Seed one history row so range queries have something to read.
        doc = self.storage._load_doc()
        doc.setdefault("history", []).append({
            "day": "2026-09-14", "subject": "MATH", "topic": "Seed",
            "minutes": 25, "questions": 0, "pages": 0, "ts": 1,
        })
        self.storage._save_doc(doc)

    def tearDown(self):
        self.tmp.cleanup()
        if self.prev_data_dir is None:
            os.environ.pop("OZTUDY_DATA_DIR", None)
        else:
            os.environ["OZTUDY_DATA_DIR"] = self.prev_data_dir
        import importlib

        import storage as storage_mod

        importlib.reload(storage_mod)

    def test_parallel_reads_and_writes(self):
        storage = self.storage
        errors: list[BaseException] = []
        lock = threading.Lock()

        def reader(n: int):
            try:
                for i in range(30):
                    doc = storage._load_doc()
                    self.assertIsInstance(doc, dict)
                    storage.query_history_range("2026-01-01", "2026-12-31")
            except BaseException as exc:  # noqa: BLE001 — collected, asserted below
                with lock:
                    errors.append(exc)

        def writer(n: int):
            try:
                for i in range(30):
                    doc = storage._load_doc()
                    hist = doc.setdefault("history", [])
                    hist.append({
                        "day": "2026-09-14", "subject": "MATH",
                        "topic": f"T{n}-{i}", "minutes": 5,
                        "questions": 0, "pages": 0, "ts": i,
                    })
                    # Keep the table bounded so the stress stays fast.
                    del hist[:-50]
                    storage._save_doc(doc)
            except BaseException as exc:  # noqa: BLE001
                with lock:
                    errors.append(exc)

        threads = (
            [threading.Thread(target=reader, args=(i,)) for i in range(6)]
            + [threading.Thread(target=writer, args=(i,)) for i in range(4)]
        )
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
            self.assertFalse(t.is_alive(), "worker thread hung (possible deadlock)")
        self.assertEqual(errors, [], f"concurrent store access raised: {errors!r}")

        # Store must still be readable and well-formed afterwards.
        doc = storage._load_doc()
        self.assertIsInstance(doc.get("history", []), list)


if __name__ == "__main__":
    unittest.main()
