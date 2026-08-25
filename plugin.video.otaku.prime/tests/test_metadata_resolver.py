import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.database.metadata_provider import MetadataProviderStore
from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.services.metadata_resolver import MetadataResolverService
from resources.lib.services.watchlist_sync import WatchlistSyncService
from resources.lib.users import UserStore


def stamp(year, month, day):
    return int(datetime.datetime(
        year, month, day, tzinfo=datetime.timezone.utc
    ).timestamp())


class FakeMetadataClient:
    def test_connection(self):
        return {"provider": "tmdb", "ok": True}

    def search_series(self, title, year=None):
        return [{
            "id": 100,
            "name": "Franchise",
            "original_name": "Franchise",
            "year": 2020,
        }]

    def get_show(self, show_id):
        self._check_show(show_id)
        return {
            "id": 100,
            "name": "Franchise",
            "original_name": "Franchise",
            "year": 2020,
            "seasons": [
                {"id": 900, "number": 0, "name": "Specials", "air_date": "2020-06-01"},
                {"id": 902, "number": 2, "name": "The Northern War", "air_date": "2021-01-10"},
            ],
        }

    def get_season(self, show_id, season_number, season_id=None):
        self._check_show(show_id)
        if int(season_number) == 0:
            return {
                "id": 900,
                "number": 0,
                "name": "Specials",
                "air_date": "2020-06-01",
                "episodes": [
                    {"id": 1001, "number": 1, "name": "Recap", "air_date": "2020-03-01"},
                    {"id": 1007, "number": 7, "name": "Bonus Story", "air_date": "2020-06-01"},
                ],
            }
        if int(season_number) == 2:
            return {
                "id": 902,
                "number": 2,
                "name": "The Northern War",
                "air_date": "2021-01-10",
                "episodes": [
                    {"id": 2001, "number": 1, "name": "Arrival", "air_date": "2021-01-10"},
                    {"id": 2002, "number": 2, "name": "Battle", "air_date": "2021-01-17"},
                ],
            }
        raise AssertionError("unexpected season {}".format(season_number))

    @staticmethod
    def _check_show(show_id):
        if int(show_id) != 100:
            raise AssertionError("unexpected show {}".format(show_id))


class MetadataResolverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "users.sqlite")
        UserStore(self.db_path).initialize()
        self.media = WatchlistMediaStore(self.db_path)
        self.media.initialize()
        self.config = MetadataProviderStore(self.db_path)
        self.config.initialize()
        self.config.save_tmdb("api_key", "unit-test-key", verified_at=1)
        self.resolver = MetadataResolverService(
            self.config,
            client_factory=lambda provider, config: FakeMetadataClient(),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _franchise(self):
        return self.media.upsert_tv_series(
            english_name="Franchise",
            romaji_name="Franchise",
            anilist_root_id=1,
            franchise_resolved=True,
        )

    def _row(self, table, local_id):
        with sqlite3.connect(self.db_path) as db:
            db.row_factory = sqlite3.Row
            return dict(db.execute(
                "SELECT * FROM {} WHERE local_id=?".format(table),
                (local_id,),
            ).fetchone())

    def test_resolves_franchise_named_season_and_normal_episodes(self):
        series = self._franchise()
        season = self.media.upsert_season(
            series,
            2,
            english_name="Completely Different Arc Title",
            anilist_id=2,
            release_date="2021-01-10",
            media_category="tv",
            kodi_season_number=2,
            kodi_resolved=False,
        )
        self.media.save_provider_list_status("season", season, "anilist", "CURRENT")
        episode1 = self.media.upsert_episode(season, 1)
        episode2 = self.media.upsert_episode(season, 2)
        self.media.schedule_release("episode", episode1, stamp(2021, 1, 10))
        self.media.schedule_release("episode", episode2, stamp(2021, 1, 17))

        result = self.resolver.run_once()

        self.assertEqual(1, result["resolved"])
        self.assertEqual(0, result["unresolved"])
        season_row = self._row("seasons", season)
        self.assertEqual("tmdb", season_row["metadata_provider"])
        self.assertEqual("902", season_row["metadata_season_id"])
        self.assertEqual(2, season_row["kodi_season_number"])
        self.assertEqual("The Northern War", season_row["kodi_season_name"])
        self.assertEqual(1, season_row["kodi_resolved"])
        self.assertEqual(1, self._row("episodes", episode1)["kodi_episode_number"])
        self.assertEqual("Arrival", self._row("episodes", episode1)["kodi_episode_name"])
        self.assertEqual(2, self._row("episodes", episode2)["kodi_episode_number"])

        series_row = self._row("tv_series", series)
        self.assertEqual("tmdb", series_row["metadata_provider"])
        self.assertEqual("100", series_row["metadata_show_id"])
        self.assertEqual("Franchise", series_row["metadata_show_name"])
        self.assertEqual(2020, series_row["metadata_show_year"])

    def test_special_uses_provider_owned_s00_number_not_local_episode_number(self):
        series = self._franchise()
        special = self.media.upsert_season(
            series,
            3,
            english_name="Bonus Story",
            anilist_id=30,
            release_date="2020-06-01",
            media_category="ova",
            kodi_season_number=0,
            kodi_resolved=False,
        )
        self.media.save_provider_list_status("season", special, "anilist", "COMPLETED")
        episode = self.media.upsert_episode(special, 1)
        self.media.schedule_release("episode", episode, stamp(2020, 6, 1))

        result = self.resolver.run_once()

        self.assertEqual(1, result["resolved"])
        special_row = self._row("seasons", special)
        episode_row = self._row("episodes", episode)
        self.assertEqual(0, special_row["kodi_season_number"])
        self.assertEqual("Specials", special_row["kodi_season_name"])
        self.assertEqual(1, special_row["kodi_resolved"])
        self.assertEqual(7, episode_row["kodi_episode_number"])
        self.assertEqual("1007", episode_row["metadata_episode_id"])
        self.assertEqual("Bonus Story", episode_row["kodi_episode_name"])

    def test_ambiguous_special_is_left_unresolved_instead_of_becoming_s00e01(self):
        series = self._franchise()
        special = self.media.upsert_season(
            series,
            3,
            english_name="Unknown OVA",
            anilist_id=31,
            media_category="ova",
            kodi_season_number=0,
            kodi_resolved=False,
        )
        self.media.save_provider_list_status("season", special, "anilist", "PLANNING")
        episode = self.media.upsert_episode(special, 1)

        result = self.resolver.run_once()

        self.assertEqual(0, result["resolved"])
        self.assertEqual(1, result["unresolved"])
        self.assertEqual(0, self._row("seasons", special)["kodi_resolved"])
        self.assertIsNone(self._row("episodes", episode)["kodi_episode_number"])

    def test_watchlist_fetch_is_blocked_until_metadata_provider_is_configured(self):
        class Gate:
            def is_configured(self):
                return False
            def status(self):
                return {"configured": False, "provider": None}

        class Importer:
            calls = 0
            def sync(self):
                self.calls += 1
                return {"imported": 1}

        importer = Importer()
        result = WatchlistSyncService([importer], gate=Gate()).run_once()

        self.assertEqual(0, importer.calls)
        self.assertEqual("metadata_provider_required", result[0]["blocked"])


if __name__ == "__main__":
    unittest.main()
