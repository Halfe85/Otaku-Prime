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
    def _paths(self, root):
        return os.path.join(root, "prime.sqlite"), os.path.join(root, "system", "identity.json")

    def test_rating_normalization_handles_anime_rating_labels(self):
        self.assertEqual("RX", normalize_rating({"age_rating": "Rx - Hentai"}))
        self.assertEqual("R+", normalize_rating({"age_rating": "R+ - Mild Nudity"}))
        self.assertEqual(
            "R+", normalize_rating({"age_rating": "R+ - Mild Nudity", "mature": True})
        )
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
        with tempfile.TemporaryDirectory() as root:
            path, profile = self._paths(root)
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE watchlist_preferences(
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  mature INTEGER NOT NULL DEFAULT 0 CHECK(mature IN(0,1)),
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                db.execute("INSERT INTO watchlist_preferences(singleton,mature) VALUES(1,1)")
            store = AgeContentPolicyStore(path, system_profile_path=profile)
            store.initialize()
            state = store.state()
            self.assertIsNone(state["age"])
            self.assertEqual(0, state["mature"])
            self.assertFalse(state["mature_allowed"])
            self.assertFalse(state["birth_date_locked"])

    def test_mature_switch_only_enables_for_adult_age(self):
        with tempfile.TemporaryDirectory() as root:
            path, profile = self._paths(root)
            store = AgeContentPolicyStore(path, system_profile_path=profile)
            store.initialize()
            today = datetime.date.today()
            try:
                born = today.replace(year=today.year - 20)
            except ValueError:
                born = today.replace(month=2, day=28, year=today.year - 20)
            state = store.set_birth_date(born.strftime("%d/%m/%Y"))
            self.assertTrue(state["birth_date_locked"])
            self.assertTrue(state["storage_persistent"])
            state = store.set_mature(1)
            self.assertGreaterEqual(state["age"], 18)
            self.assertTrue(state["mature_allowed"])
            self.assertEqual(1, state["mature"])

    def test_birth_date_cannot_be_changed_after_first_save(self):
        with tempfile.TemporaryDirectory() as root:
            path, profile = self._paths(root)
            store = AgeContentPolicyStore(path, system_profile_path=profile)
            store.set_birth_date("01/01/2000")
            with self.assertRaises(ValueError):
                store.set_birth_date("02/01/2000")
            state = store.state()
            self.assertEqual("2000-01-01", state["birth_date"])
            self.assertTrue(state["birth_date_locked"])

    def test_system_profile_survives_addon_database_destruction(self):
        with tempfile.TemporaryDirectory() as root:
            path, profile = self._paths(root)
            first = AgeContentPolicyStore(path, system_profile_path=profile)
            first.set_birth_date("01/01/2000")
            self.assertTrue(os.path.isfile(profile))

            # Simulate deleting/reinstalling the addon database while keeping the
            # operating-system user's persistent identity file.
            os.unlink(path)
            second = AgeContentPolicyStore(path, system_profile_path=profile)
            state = second.state()
            self.assertEqual("2000-01-01", state["birth_date"])
            self.assertTrue(state["birth_date_locked"])
            self.assertTrue(state["storage_persistent"])
            # Mature is intentionally an addon preference and resets OFF.
            self.assertEqual(0, state["mature"])

    def test_existing_alpha_database_birth_date_migrates_outside_addon(self):
        with tempfile.TemporaryDirectory() as root:
            path, profile = self._paths(root)
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE prime_age_preferences(
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  birth_date TEXT,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                db.execute(
                    "INSERT INTO prime_age_preferences(singleton,birth_date) VALUES(1,?)",
                    ("2000-01-01",),
                )
            store = AgeContentPolicyStore(path, system_profile_path=profile)
            state = store.state()
            self.assertEqual("2000-01-01", state["birth_date"])
            self.assertTrue(state["birth_date_locked"])
            self.assertTrue(os.path.isfile(profile))
            with sqlite3.connect(path) as db:
                value = db.execute(
                    "SELECT birth_date FROM prime_age_preferences WHERE singleton=1"
                ).fetchone()[0]
            self.assertIsNone(value)


if __name__ == "__main__":
    unittest.main()
