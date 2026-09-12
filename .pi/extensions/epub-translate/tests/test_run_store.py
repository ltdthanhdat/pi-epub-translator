import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from run_store import LEASE_SECONDS, RunStore


class RunStoreLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tempdir.name)
        self.store = RunStore(self.tmp / "run.sqlite")
        self.store.create_run(
            {
                "model": "test/model",
                "thinking": "high",
                "target_language": "Vietnamese",
                "target_language_code": "vi",
                "max_attempts": 2,
            }
        )
        self.store.add_jobs(
            [
                {
                    "xhtml_path": "OEBPS/text/chapter.xhtml",
                    "locator": "0",
                    "input_path": "inputs/1.xhtml",
                },
                {
                    "xhtml_path": "OEBPS/text/chapter.xhtml",
                    "locator": "1",
                    "input_path": "inputs/2.xhtml",
                },
            ]
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_claim_returns_distinct_jobs_for_concurrent_workers(self):
        first = self.store.claim("worker-a", now=100)
        second = self.store.claim("worker-b", now=100)

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(1, first.attempts)
        self.assertEqual(1, second.attempts)

    def test_expired_lease_is_reissued_with_new_token(self):
        first = self.store.claim("worker-a", now=100)
        second = self.store.claim("worker-b", now=100 + LEASE_SECONDS + 1)

        self.assertEqual(first.id, second.id)
        self.assertNotEqual(first.lease_token, second.lease_token)
        self.assertEqual(2, second.attempts)

    def test_heartbeat_requires_current_worker_and_token(self):
        claimed = self.store.claim("worker-a", now=100)

        self.assertFalse(self.store.heartbeat(claimed.id, "worker-b", claimed.lease_token, now=110))
        self.assertFalse(self.store.heartbeat(claimed.id, "worker-a", "stale-token", now=110))
        self.assertTrue(self.store.heartbeat(claimed.id, "worker-a", claimed.lease_token, now=110))

    def test_stale_worker_cannot_complete_after_reclaim(self):
        old = self.store.claim("worker-a", now=100)
        current = self.store.claim("worker-b", now=100 + LEASE_SECONDS + 1)

        self.assertFalse(self.store.complete(old.id, "worker-a", old.lease_token, "results/old.xhtml"))
        self.assertTrue(self.store.complete(current.id, "worker-b", current.lease_token, "results/current.xhtml"))

    def test_cancel_blocks_new_claims_and_retry_resets_only_failed_jobs(self):
        claimed = self.store.claim("worker-a", now=100)
        self.assertTrue(self.store.fail(claimed.id, "worker-a", claimed.lease_token, "bad translation"))
        self.assertTrue(self.store.request_cancel())
        self.assertIsNone(self.store.claim("worker-b", now=101))

        self.assertTrue(self.store.clear_cancel())
        self.assertEqual(1, self.store.retry_failed())
        retried = self.store.claim("worker-c", now=102)
        self.assertEqual(claimed.id, retried.id)
        self.assertEqual(1, retried.attempts)

    def test_only_one_worker_can_claim_merge(self):
        first = self.store.claim("worker-a", now=100)
        second = self.store.claim("worker-b", now=100)
        self.assertTrue(self.store.complete(first.id, "worker-a", first.lease_token, "results/1.xhtml"))
        self.assertTrue(self.store.complete(second.id, "worker-b", second.lease_token, "results/2.xhtml"))

        self.assertTrue(self.store.try_claim_merge("worker-a"))
        self.assertFalse(self.store.try_claim_merge("worker-b"))


if __name__ == "__main__":
    unittest.main()
