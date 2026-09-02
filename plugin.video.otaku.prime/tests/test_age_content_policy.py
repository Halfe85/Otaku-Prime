from __future__ import annotations

import datetime
import os
import sqlite3
import tempfile
import unittest

from resources.lib.services.age_content_policy import (
    AgeContentPolicyStore,
    evaluate_content,
    normalize_rating,
    parse_birth_date,
)


class AgeContentPolicyTests(unittest.TestCase):
    def test_rating_normalization_handles_anime_rating_labels(self):
        self.assertEqual("RX", normalize_rating({"age_rating": "Rx - Hentai"}))
        self.assertEqual("R+", normalize_rating({"age_rating": "R+ - Mild Nudity"}))
        self.assertEqual("R", normalize_rating({"age_rating": "R - 17+"}))
        self.assertEqual("PG-13", normalize_rating({"age_rating": "PG-13"}))
        self.assertEqual("RX", normalize_rating({"genres": ["Drama", "Hentai"]}))

    def test_missing_age_keeps_only_unrestricted_ratings_eligible(self):
        self.assertTrue(evaluate_content({"age_rating": "G"}, age=None)["kodi_allowed"])
        self.assertTrue(evaluate_content({"age_rating": "PG"}, age=None)["kodi_allowed"])
        pg13 = evaluate_content({"age_rating": "PG-13"}, age=None)
        self.assertFalse(pg13["kodi_allowed"])
        self.assertFalse(pg13["blur_ui"])
        rplus = evaluate_content({"age_rating": "R+"}, age=None)
        self.assertFalse(rplus["kodi_allowed"])
        self.assertTrue(rplus["blur_ui"])
        rx = evaluate_content({"age_rating": "Rx"}, age=None, mature_enabled=True)
        self.assertFalse(rx["kodi_allowed"])
        self.assertTrue(rx["blur_ui"])

    def test_exact_age_thresholds(self):
        self.assertFalse(evaluate_content({"age_rating": "PG-13"}, age=9)["kodi_allowed"])
        self.assertTrue(evaluate_content({"age_rating": "PG-13"}, age=10)["kodi_allowed"])
        self.assertFalse(evaluate_content({"age_rating": "R+"}, age=14)["kodi_allowed"])
        self.assertTrue(evaluate_content({"age_rating": "R+"}, age=15)["kodi_allowed"])
        self.assertFalse(evaluate_content(
            {"age_rating": "Rx"}, age=18, mature_enabled=False
        )["kodi_allowed"])
        self.assertTrue(evaluate_content(
            {"age_rating": "Rx"}, age=18, mature_enabled=True
        )["kodi_allowed"])

    def test_birth_date_requires_dd_mm_yyyy(self):
        self.assertEqual("2000-12-31", parse_birth_date("31/12/2000"))
        with self.assertRaises(ValueError):
            parse_birth_date("2000-12-31")
        with self.assertRaises(ValueError):
            parse_birth_date("31/02/2000")

    def test_upgrade_without_birth_date_forces_old_mature_flag_off(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        path = handle.name
        handle.close()
        try:
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE watchlist_preferences(
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  mature INTEGER NOT NULL DEFAULT 0 CHECK(mature IN(0,1)),
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                db.execute("INSERT INTO watchlist_preferences(singleton,mature) VALUES(1,1)")
            store = AgeContentPolicyStore(path)
            store.initialize()
            state = store.state()
            self.assertIsNone(state["age"])
            self.assertEqual(0, state["mature"])
            self.assertFalse(state["mature_allowed"])
        finally:
            os.unlink(path)

    def test_mature_switch_only_enables_for_adult_age(self):
        handle = tempfile.NamedTemporaryFile(delete=False)
        path = handle.name
        handle.close()
        try:
            store = AgeContentPolicyStore(path)
            store.initialize()
            today = datetime.date.today()
            born = today.replace(year=today.year - 20)
            store.set_birth_date(born.strftime("%d/%m/%Y"))
            state = store.set_mature(1)
            self.assertGreaterEqual(state["age"], 18)
            self.assertTrue(state["mature_allowed"])
            self.assertEqual(1, state["mature"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
