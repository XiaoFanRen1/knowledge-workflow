import concurrent.futures
import time
import unittest
from knowledge_workflow import jobs
from knowledge_workflow.storage import Store
from support import Fixture


def finish(config, job_id):
    until = time.monotonic() + 25
    while time.monotonic() < until:
        result = jobs.status(config, job_id)
        if result["state"] in jobs.TERMINAL:
            return result
        time.sleep(.1)
    raise AssertionError("maintenance did not terminate")


class Maintenance(unittest.TestCase):
    def setUp(self):
        self.f = Fixture()
        self.addCleanup(self.f.close)
        from knowledge_workflow import runner
        runner.start(self.f.config)
        self.f.capture()

    def test_short_submission_idempotence_and_publication(self):
        started = time.monotonic()
        first = jobs.submit(self.f.config, "maint-1", lexical_only=True)
        self.assertLess(time.monotonic() - started, 2)
        second = jobs.submit(self.f.config, "maint-1", lexical_only=True)
        self.assertEqual(first["job_id"], second["job_id"])
        self.assertTrue(second["duplicate"])
        with self.assertRaisesRegex(ValueError, "operation_id_conflict"):
            jobs.submit(self.f.config, "maint-1", lexical_only=False)
        done = finish(self.f.config, first["job_id"])
        self.assertEqual(done["state"], "published", done)
        self.assertEqual(self.f.service.status()["pending_capture_count"], 0)
        self.assertEqual(jobs.cancel(self.f.config, first["job_id"])["state"], "published")

    def test_two_submitters_share_one_writer(self):
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(jobs.submit, self.f.config, f"maint-{i}", lexical_only=True) for i in range(2)]
            results = [f.result() for f in futures]
        self.assertEqual(sum(item["ok"] for item in results), 1, results)
        self.assertEqual(results[0]["job_id"], results[1]["job_id"])
        self.assertEqual(finish(self.f.config, results[0]["job_id"])["state"], "published")

    def test_cancel_and_initialization_failure_keep_previous(self):
        before = Store(self.f.config).current.read_bytes()
        job = jobs.submit(self.f.config, "cancel-1")
        jobs.cancel(self.f.config, job["job_id"])
        done = finish(self.f.config, job["job_id"])
        self.assertIn(done["state"], {"cancelled", "failed"}, done)
        self.assertEqual(Store(self.f.config).current.read_bytes(), before)

    def test_missing_model_fails_without_publishing_lexical_success(self):
        before = Store(self.f.config).current.read_bytes()
        job = jobs.submit(self.f.config, "needs-model")
        done = finish(self.f.config, job["job_id"])
        self.assertEqual(done["state"], "failed", done)
        self.assertEqual(Store(self.f.config).current.read_bytes(), before)
        self.assertIn("model", done["error"]["message"])

    def test_unknown_and_other_library_jobs_rejected(self):
        other = Fixture()
        self.addCleanup(other.close)
        job = jobs.submit(self.f.config, "only-here", lexical_only=True)
        with self.assertRaisesRegex(ValueError, "unknown"):
            jobs.status(other.config, job["job_id"])
        with self.assertRaises(ValueError):
            jobs.cancel(self.f.config, "../outside")
        finish(self.f.config, job["job_id"])
