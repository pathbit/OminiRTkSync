"""Testes unitários para o CronScheduler do OminiRTKSync."""

import time
import unittest

from omini_rtksync.cron import CronScheduler


class TestOminiCron(unittest.TestCase):
    def test_cron_trigger_and_history(self):
        calls = []

        def mock_sync():
            calls.append(time.time())
            return {"success": True, "total": 4, "refreshed": 1}

        cron = CronScheduler(sync_callback=mock_sync, interval_seconds=60, name="OminiTestCron")
        status_init = cron.get_status()
        self.assertFalse(status_init["active"])
        self.assertEqual(status_init["totalRuns"], 0)

        res = cron.trigger_now()
        self.assertTrue(res["success"])
        self.assertEqual(res["totalInspected"], 4)
        self.assertEqual(res["refreshedCount"], 1)
        self.assertEqual(len(calls), 1)

        status_after = cron.get_status()
        self.assertEqual(status_after["totalRuns"], 1)
        self.assertEqual(status_after["totalRenewals"], 1)
        self.assertEqual(len(status_after["history"]), 1)


if __name__ == "__main__":
    unittest.main()
