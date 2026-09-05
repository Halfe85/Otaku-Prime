import io
import logging
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError

from resources.lib.database.app_logs import AppLogStore
from resources.lib.logging_config import configure_logging, get_logger, PrimeFileHandler
from resources.lib.services.mediator_helper_simkl import SimklMediatorClient, MediatorPlacementError
from resources.lib.services.mediator_trace import (
    MediatorTrace, mediation_log_context, current_mediation_trace, placement_facts,
)
from resources.lib.services.mediator_simkl_strict import StrictStructuralSimklMediatorEndpoint
from tests.test_simkl_strict_mediator import FakeClient, target, owner, episode


class MediationLoggingTests(unittest.TestCase):
    def tearDown(self):
        logger = logging.getLogger("otaku_prime")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    def test_full_records_survive_ui_retention_and_database_detach(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AppLogStore(os.path.join(directory, "users.sqlite"), max_entries=100)
            store.initialize()
            configure_logging(store)
            log = get_logger("test")
            log.info("long-evidence:" + "x" * 5000 + ":end-of-evidence")
            for index in range(105):
                log.info("item %s", index)
            self.assertFalse(any("long-evidence" in row["message"] for row in store.list()))
            configure_logging()  # Service detaches SQLite before stopping workers.
            log.warning("shutdown still recorded")
            with open(os.path.join(directory, "logs", "prime.log")) as stream:
                data = stream.read()
            self.assertIn("x" * 5000 + ":end-of-evidence", data)
            self.assertIn("shutdown still recorded", data)

    def test_rotation_and_reconfiguration_do_not_duplicate_records(self):
        with tempfile.TemporaryDirectory() as directory:
            configure_logging(log_directory=directory)
            configure_logging(log_directory=directory)
            handlers = [h for h in logging.getLogger("otaku_prime").handlers
                        if isinstance(h, PrimeFileHandler)]
            self.assertEqual(1, len(handlers))
            handlers[0].maxBytes = 1024
            for index in range(20):
                get_logger("test").info("rotation-%s %s", index, "x" * 600)
            archives = [name for name in os.listdir(directory) if name.startswith("prime.log")]
            self.assertEqual(6, len(archives))
            with open(os.path.join(directory, "prime.log")) as stream:
                self.assertEqual(1, stream.read().count("rotation-19"))

    def test_archive_failure_does_not_disable_kodi_logging(self):
        with tempfile.TemporaryDirectory() as directory:
            rows = []
            with tempfile.NamedTemporaryFile(dir=directory) as file:
                configure_logging(kodi_writer=lambda *row: rows.append(row), log_directory=file.name)
            get_logger("test").error("still visible")
            self.assertTrue(any("Diagnostic file logging unavailable" in row[2] for row in rows))
            self.assertTrue(any(row[2] == "still visible" for row in rows))

    def test_trace_redacts_credentials_and_excludes_repeated_staff_bios(self):
        with self.assertLogs("otaku_prime.services-mediator_trace", level="INFO") as logs:
            MediatorTrace("watch-1").info("TEST", "EVIDENCE", {
                "api_key": "private-api", "client_id": "private-client",
                "nested": {"access_token": "private-token"}, "simkl_id": "100"})
        data = "\n".join(logs.output)
        for secret in ("private-api", "private-client", "private-token"):
            self.assertNotIn(secret, data)
        self.assertIn('"simkl_id":"100"', data)
        facts = placement_facts({"season": {"number": 1, "cast": [{"biography": "huge"}]}})
        self.assertNotIn("cast", facts["season"])
        self.assertEqual(1, facts["season_credit_count"])

    def test_request_success_cache_failure_and_thread_context(self):
        client = SimklMediatorClient(client_id="secret-key", request_delay=0,
            opener=lambda *args, **kwargs: io.BytesIO(b'{"ids":{"simkl":100}}'))
        with self.assertLogs("otaku_prime.services-mediator_trace", level="INFO") as logs:
            with mediation_log_context("watch-1"):
                client.anime(100)
                client.anime(100)
                def fail(*args, **kwargs):
                    raise HTTPError("https://example.test/?client_id=secret-key", 429, "limited", {}, None)
                client._open = fail
                with self.assertRaises(MediatorPlacementError):
                    client.anime(200)
                with ThreadPoolExecutor(max_workers=1) as workers:
                    self.assertIsNone(workers.submit(current_mediation_trace).result())
                with mediation_log_context("watch-2"):
                    self.assertEqual("watch-2", current_mediation_trace().watchlist_local_id)
                self.assertEqual("watch-1", current_mediation_trace().watchlist_local_id)
        self.assertIsNone(current_mediation_trace())
        data = "\n".join(logs.output)
        for event in ("REQUEST", "RESPONSE", "CACHE_HIT", "FAILED"):
            self.assertIn("event=" + event, data)
        self.assertIn('"status":429', data)
        self.assertIn("MEDIATOR[watch-1]", data)
        self.assertNotIn("secret-key", data)

    def test_coordinate_evidence_is_logged_before_gap_failure(self):
        client = FakeClient(target(), [episode(1, 1, 1), episode(2, 1, 3)], owner())
        with self.assertLogs("otaku_prime.services-mediator_trace", level="INFO") as logs:
            with self.assertRaisesRegex(MediatorPlacementError, "gaps"):
                StrictStructuralSimklMediatorEndpoint(client).resolve({"local_id": "watch-gap", "simkl_id": "100"})
        data = "\n".join(logs.output)
        self.assertIn("MEDIATOR[watch-gap]", data)
        self.assertIn("event=ROWS_RECEIVED", data)
        self.assertIn('"episode_number":3', data)
        self.assertIn("event=CANDIDATE_REJECTED", data)


if __name__ == "__main__":
    unittest.main()
