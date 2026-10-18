import tempfile
import unittest
from pathlib import Path

from service.jobs import JobStore, QueueFull


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = JobStore(self.root / "jobs.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_queue_is_persistent_and_public_status_is_processing(self):
        self.store.enqueue("abc", "/input.mov", {"duration_sec": 2})
        other = JobStore(self.root / "jobs.sqlite3")
        self.assertEqual(other.public_job("abc"), {"job_id": "abc", "status": "processing"})

    def test_claim_does_not_return_the_same_job_twice(self):
        self.store.enqueue("abc", "/input.mov", {})
        self.assertEqual(self.store.claim()["job_id"], "abc")
        self.assertIsNone(self.store.claim())

    def test_restart_requeues_interrupted_job(self):
        self.store.enqueue("abc", "/input.mov", {})
        self.store.claim()
        self.store.recover()
        self.assertEqual(self.store.claim()["attempts"], 2)

    def test_repeated_crashes_end_as_failed_not_no_violation(self):
        self.store.enqueue("abc", "/input.mov", {})
        self.store.claim()
        self.store.recover()
        self.store.claim()
        self.store.recover()
        job = self.store.public_job("abc")
        self.assertEqual(job["status"], "failed")
        self.assertNotIn("verdict", job)

    def test_successful_result_survives_reopening_database(self):
        self.store.enqueue("abc", "/input.mov", {})
        self.store.claim()
        self.store.finish("abc", {"verdict": "no_violation", "violations": []})
        self.assertEqual(JobStore(self.root / "jobs.sqlite3").public_job("abc")["verdict"], "no_violation")

    def test_queue_limit_is_enforced_atomically(self):
        self.store.enqueue("abc", "/input.mov", {}, max_pending=1)
        with self.assertRaises(QueueFull):
            self.store.enqueue("def", "/another.mov", {}, max_pending=1)

    def test_cancel_queued_is_idempotent_frees_capacity_and_survives_restart(self):
        self.store.enqueue('cancel', '/input.mov', {}, max_pending=1)
        first=self.store.cancel('cancel')
        self.assertEqual(first, self.store.cancel('cancel'))
        self.assertEqual(first['status'], 'failed')
        self.assertEqual(first['error'], 'Analysis cancelled by user')
        self.assertNotIn('verdict', first)
        other=JobStore(self.root/'jobs.sqlite3');other.recover()
        self.assertTrue(other.is_cancelled('cancel'))
        self.assertIsNone(other.claim())
        other.enqueue('next', '/next.mov', {}, max_pending=1)
        self.assertEqual(other.claim()['job_id'], 'next')

    def test_cancel_running_cannot_be_overwritten_by_result_or_failure(self):
        from service.cancellation import AnalysisCancelled
        self.store.enqueue('cancel', '/input.mov', {})
        self.store.claim();self.store.cancel('cancel')
        with self.assertRaises(AnalysisCancelled):
            self.store.finish('cancel', {'verdict':'no_violation','violations':[]})
        self.store.fail('cancel', 'another error')
        self.assertEqual(self.store.public_job('cancel')['error'], 'Analysis cancelled by user')

    def test_cancel_preserves_result_if_completion_wins_race(self):
        self.store.enqueue('done', '/input.mov', {});self.store.claim()
        self.store.finish('done', {'verdict':'no_violation','violations':[]})
        self.assertEqual(self.store.cancel('done')['status'], 'done')
        self.assertFalse(self.store.is_cancelled('done'))
        self.assertIsNone(self.store.cancel('unknown'))


if __name__ == "__main__":
    unittest.main()
