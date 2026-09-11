import concurrent.futures
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_workflow.util import atomic_json, read_json


class AtomicFiles(unittest.TestCase):
    def test_reader_retries_sharing_failure_then_returns_complete_json(self):
        with patch.object(Path, "read_text", side_effect=[PermissionError(13, "synthetic sharing collision"), '{"generation":"new"}']) as read, \
                patch("knowledge_workflow.util.time.sleep") as sleep:
            self.assertEqual(read_json(Path("current.json")), {"generation": "new"})
            self.assertEqual(read.call_count, 2)
            sleep.assert_called_once_with(.01)

    def test_permanent_replace_error_keeps_old_pointer_and_removes_temporary(self):
        with tempfile.TemporaryDirectory(prefix="kw-atomic-test-") as directory:
            root = Path(directory)
            target = root / "current.json"
            atomic_json(target, {"generation": "old"})
            before = target.read_bytes()
            with patch("knowledge_workflow.util.os.replace", side_effect=PermissionError(13, "synthetic denied")) as replace, \
                    patch("knowledge_workflow.util.time.sleep") as sleep:
                with self.assertRaises(PermissionError):
                    atomic_json(target, {"generation": "new"})
                self.assertEqual(replace.call_count, 6)
                self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list), .31)
            self.assertEqual(target.read_bytes(), before)
            self.assertEqual(list(root.iterdir()), [target])

    def test_transient_replace_error_publishes_once_released(self):
        real_replace = os.replace
        attempts = []
        def contested(source, target):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError(13, "synthetic reader sharing collision")
            return real_replace(source, target)
        with tempfile.TemporaryDirectory(prefix="kw-atomic-test-") as directory:
            target = Path(directory) / "current.json"
            atomic_json(target, {"generation": "old"})
            with patch("knowledge_workflow.util.os.replace", side_effect=contested):
                atomic_json(target, {"generation": "new"})
            self.assertEqual(read_json(target), {"generation": "new"})
            self.assertEqual(len(attempts), 2)

    def test_bad_json_is_fatal_without_retry(self):
        with patch.object(Path, "read_text", return_value="broken") as read, patch("knowledge_workflow.util.time.sleep") as sleep:
            with self.assertRaises(json.JSONDecodeError):
                read_json(Path("current.json"))
            self.assertEqual(read.call_count, 1)
            sleep.assert_not_called()

    def test_concurrent_readers_observe_only_complete_generations(self):
        with tempfile.TemporaryDirectory(prefix="kw-atomic-test-") as directory:
            target = Path(directory) / "current.json"
            atomic_json(target, {"number": 0, "body": "0" * 4096})
            barrier = threading.Barrier(4)
            def reader():
                barrier.wait(timeout=5)
                for _ in range(500):
                    value = read_json(target)
                    self.assertEqual(value["body"], str(value["number"]) * 4096)
            with concurrent.futures.ThreadPoolExecutor(3) as pool:
                futures = [pool.submit(reader) for _ in range(3)]
                barrier.wait(timeout=5)
                for number in range(1, 101):
                    atomic_json(target, {"number": number, "body": str(number) * 4096})
                for future in futures:
                    future.result(timeout=10)
            self.assertEqual(read_json(target)["number"], 100)
