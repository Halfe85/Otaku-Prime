import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.database.app_logs import AppLogStore
from resources.lib.logging_config import configure_logging,get_logger


class AppLogStoreTests(unittest.TestCase):
    def test_log_stream_is_bounded_and_supports_incremental_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppLogStore(os.path.join(directory, "db.sqlite"), max_entries=100)
            store.initialize()
            for number in range(105):
                store.write("INFO", "test", "message {}".format(number))
            entries = store.list(limit=200)
            self.assertEqual(100, len(entries))
            self.assertEqual("message 5", entries[0]["message"])
            self.assertEqual(
                ["message 104"],
                [row["message"] for row in store.list(after_id=entries[-2]["id"])],
            )

    def test_central_logger_persists_info_warning_and_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store=AppLogStore(os.path.join(directory,"db.sqlite")); store.initialize()
            configure_logging(app_log_store=store)
            logger=get_logger("test-levels")
            logger.info("information"); logger.warning("warning"); logger.error("failure")
            self.assertEqual(["INFO","WARNING","ERROR"],
              [row["level"] for row in store.list()])
            configure_logging()


if __name__ == "__main__":
    unittest.main()
