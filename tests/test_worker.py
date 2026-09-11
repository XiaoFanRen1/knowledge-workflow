import concurrent.futures
import sys
import tempfile
import time
import unittest
from pathlib import Path

from knowledge_workflow.worker_client import ModelWorkerClient
from knowledge_workflow import jobs


class Lifecycle(unittest.TestCase):
    def make(self, delay=0, **budgets):
        temp = tempfile.TemporaryDirectory(prefix="kw-worker-test-")
        self.addCleanup(temp.cleanup)
        entry = Path(__file__).with_name("worker_fixture.py")
        client = ModelWorkerClient(entry=entry, cache=Path(temp.name),
            command=[sys.executable, "-I", "-B", "-X", "utf8", str(entry), "--prepare-delay", str(delay)], **budgets)
        self.addCleanup(client.close)
        return client

    def payload(self, text="answer", generation="fixture"):
        return {"generation": generation, "fingerprint": "fixture-key", "query": text}

    def test_wait_timeout_reuses_initialization(self):
        client = self.make(delay=.8, timeout=.25, startup_seconds=4, idle_seconds=20)
        with self.assertRaisesRegex(TimeoutError, "initializing"):
            client.call(self.payload(), "fixture-key")
        until = time.monotonic() + 4
        while client.status()["phase"] != "ready" and time.monotonic() < until:
            time.sleep(.05)
        self.assertEqual(client.call(self.payload(), "fixture-key")["value"], "answer")
        self.assertEqual(client.starts, 1)

    def test_ready_query_timeout_reclaims_launcher_and_interpreter(self):
        client = self.make(timeout=2, startup_seconds=4, idle_seconds=20)
        client.call(self.payload(), "fixture-key")
        identities = dict(client.process.kw_identities)
        self.assertGreaterEqual(len(identities), 1)
        client.timeout = .15
        with self.assertRaisesRegex(TimeoutError, "semantic_timeout"):
            client.call(self.payload("delayed"), "fixture-key")
        self.assertTrue(all(jobs._identity(pid, created) is False for pid, created in identities.items()))

    def test_generation_switch_does_not_accept_previous_worker(self):
        client = self.make(timeout=3, startup_seconds=4, idle_seconds=20)
        client.call(self.payload(), "fixture-key")
        first = dict(client.process.kw_identities)
        result = client.call(self.payload("new", "second"), "fixture-key")
        self.assertEqual(result["value"], "new")
        self.assertEqual(client.starts, 2)
        self.assertTrue(all(jobs._identity(pid, created) is False for pid, created in first.items()))

    def test_concurrent_queries_keep_their_own_responses(self):
        client = self.make(timeout=3, startup_seconds=4, idle_seconds=20)
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda value: client.call(self.payload(value), "fixture-key")["value"], ["a", "b", "c", "d"]))
        self.assertEqual(results, ["a", "b", "c", "d"])
        self.assertEqual(client.starts, 1)

    def test_close_during_initialization_finishes_owned_processes(self):
        client = self.make(delay=1, timeout=.2, startup_seconds=4, idle_seconds=20)
        with self.assertRaises(TimeoutError):
            client.call(self.payload(), "fixture-key")
        identities = dict(client.process.kw_identities)
        client.close()
        self.assertTrue(all(jobs._identity(pid, created) is False for pid, created in identities.items()))
        self.assertFalse(client.status()["cleanup_pending_pids"])

    def test_idle_reclamation(self):
        client = self.make(timeout=3, startup_seconds=4, idle_seconds=.25)
        client.call(self.payload(), "fixture-key")
        identities = dict(client.process.kw_identities)
        until = time.monotonic() + 3
        while any(jobs._identity(pid, born) for pid, born in identities.items()) and time.monotonic() < until:
            time.sleep(.05)
        self.assertTrue(all(jobs._identity(pid, created) is False for pid, created in identities.items()))
