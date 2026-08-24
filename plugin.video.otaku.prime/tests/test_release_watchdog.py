import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.database.watchlist_media import WatchlistMediaStore
from resources.lib.services.release_watchdog import ReleaseWatchdogService
from resources.lib.services.anilist_release_schedule import AniListReleaseScheduleService
from resources.lib.services.stream_library import StreamLibraryService
from resources.lib.users import UserStore


class KodiDb:
    def __init__(self):
        self.scans = []
        self.fail = False

    def scan(self, path):
        self.scans.append(path)
        if self.fail:
            raise RuntimeError("scan unavailable")


class ReleaseWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "users.sqlite")
        UserStore(self.db_path).initialize()
        self.store = WatchlistMediaStore(self.db_path)
        self.store.initialize()
        self.library = StreamLibraryService(os.path.join(self.tmp.name, "library"))
        self.kodi = KodiDb()
        self.watchdog = ReleaseWatchdogService(
            self.store, self.library, self.kodi, interval_seconds=60
        )
        self.series = self.store.upsert_tv_series(
            english_name="Show", anilist_root_id=1, franchise_resolved=True
        )
        self.season = self.store.upsert_season(
            self.series, 1, anilist_id=1, kodi_show_name="Show",
            kodi_season_number=1, kodi_resolved=True
        )
        self.store.save_provider_list_status("season", self.season, "anilist", "CURRENT")
        self.episode1 = self.store.upsert_episode(self.season, 1)
        self.episode2 = self.store.upsert_episode(self.season, 2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_requires_due_season_and_episode_schedule(self):
        self.store.schedule_release("season", self.season, 100)
        self.store.schedule_release("episode", self.episode1, 100)
        self.store.schedule_release("episode", self.episode2, 300)

        result = self.watchdog.run_once(now=200)

        self.assertEqual(1, len(result["published"]))
        self.assertTrue(result["published"][0].endswith("Show - S01E01.strm"))
        self.assertEqual([self.library.tv_series_root], self.kodi.scans)
        self.assertEqual([], self.watchdog.run_once(now=200)["published"])

    def test_missing_or_future_season_schedule_blocks_files(self):
        self.store.schedule_release("episode", self.episode1, 100)
        self.assertEqual([], self.watchdog.run_once(now=200)["published"])
        self.store.schedule_release("season", self.season, 300)
        self.assertEqual([], self.watchdog.run_once(now=200)["published"])
        self.assertEqual([], self.kodi.scans)

    def test_failed_scan_is_retried_and_not_marked_published(self):
        self.store.schedule_release("season", self.season, 100)
        self.store.schedule_release("episode", self.episode1, 100)
        self.kodi.fail = True
        failed = self.watchdog.run_once(now=200)
        self.assertEqual([], failed["published"])
        self.assertEqual(1, len(failed["failed"]))

        self.kodi.fail = False
        retried = self.watchdog.run_once(now=200)
        self.assertEqual(1, len(retried["published"]))
        self.assertEqual(2, len(self.kodi.scans))

    def test_anilist_schedule_creates_episodes_then_watchdog_publishes(self):
        class Schedule(AniListReleaseScheduleService):
            def _graphql(self, anilist_id, page):
                return {
                    "startDate": {"year": 1970, "month": 1, "day": 1},
                    "airingSchedule": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [{"episode": 3, "airingAt": 150}],
                    },
                }

        # Use a clean second season with no episodes or schedule yet.
        self.store.mark_release_schedule_checked(self.season, 200)
        season = self.store.upsert_season(
            self.series, 2, anilist_id=2, kodi_show_name="Show",
            kodi_season_number=2, kodi_resolved=True
        )
        self.store.save_provider_list_status("season", season, "anilist", "CURRENT")
        watchdog = ReleaseWatchdogService(
            self.store,
            self.library,
            self.kodi,
            schedule_service=Schedule(self.store),
        )
        result = watchdog.run_once(now=200)

        self.assertEqual(1, len(result["published"]))
        self.assertTrue(result["published"][0].endswith("Show - S02E03.strm"))


if __name__ == "__main__":
    unittest.main()
