import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.connectors.kodi_library import KodiLibraryConnector, KodiLibrarySynchronizer
from resources.lib.database.watchlist_accounts import WatchlistAccountStore
from resources.lib.database.watchlist_media import MediaIdentityConflict, WatchlistMediaStore
from resources.lib.users import UserStore
from resources.lib.watchlist.mediator import WatchStatusMediator

class MediaStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "users.sqlite")
        UserStore(self.path).initialize()
        self.store = WatchlistMediaStore(self.path)
        self.store.initialize()
        self.accounts = WatchlistAccountStore(self.path)
        self.accounts.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_hierarchical_random_hex_ids(self):
        franchise = self.store.upsert_tv_series(english_name="Frieren")
        season = self.store.upsert_season(franchise, 1, anilist_id=154587)
        episode = self.store.upsert_episode(season, 1, english_name="The Journey's End")
        self.assertRegex(franchise, r"^[0-9a-f]{32}$")
        self.assertRegex(season, "^{}-[0-9a-f]{{32}}$".format(franchise))
        self.assertRegex(episode, "^{}-[0-9a-f]{{32}}-[0-9a-f]{{32}}$".format(franchise))
        self.assertEqual(franchise, list(self.store.list_media("season"))[0]["related_series_id"])
        self.assertEqual(season, list(self.store.list_media("episode"))[0]["related_season_id"])
        self.assertEqual(1, self.store.list_tv_series_episodes(franchise)[0]["season_number"])

    def test_provider_ids_belong_to_seasons_and_merge_there(self):
        franchise = self.store.upsert_tv_series(english_name="Show")
        first = self.store.upsert_season(franchise, 1, anilist_id=1)
        same = self.store.upsert_season(franchise, 1, anilist_id=1, mal_id=2)
        self.assertEqual(first, same)
        self.assertEqual("2", list(self.store.list_media("season"))[0]["mal_id"])

    def test_conflicting_season_ids_are_rejected(self):
        franchise = self.store.upsert_tv_series(english_name="Show")
        self.store.upsert_season(franchise, 1, anilist_id=1)
        self.store.upsert_season(franchise, 2, mal_id=2)
        with self.assertRaises(MediaIdentityConflict):
            self.store.upsert_season(franchise, 3, anilist_id=1, mal_id=2)

    def test_provider_count_expands_to_episode_booleans(self):
        franchise = self.store.upsert_tv_series(english_name="Show")
        season = self.store.upsert_season(franchise, 1, mal_id=1)
        for number in range(1, 5):
            self.store.upsert_episode(season, number)
        self.store.import_provider_episode_count(season, "mal", 2)
        with sqlite3.connect(self.path) as db:
            values = [r[0] for r in db.execute("SELECT watched FROM episodes ORDER BY episode_number")]
        self.assertEqual([1, 1, 0, 0], values)

    def test_boolean_change_is_queued_and_dispatched(self):
        franchise = self.store.upsert_tv_series(english_name="Show")
        season = self.store.upsert_season(franchise, 1, anilist_id=1)
        episode = self.store.upsert_episode(season, 1)
        self.accounts.save(user_id=1, provider="anilist", external_user_id="1",
                           external_username="u", access_token="t")
        self.store.set_watch_status("episode", episode, True)
        calls = []
        class Adapter:
            def set_watch_status(self, kind, local_id, watched):
                calls.append((kind, local_id, watched))
        result = WatchStatusMediator(self.store, {"anilist": Adapter()}).dispatch_pending()
        self.assertEqual(1, result["sent"])
        self.assertEqual([("episode", episode, True)], calls)

class KodiConnectorTests(unittest.TestCase):
    def test_json_rpc_read(self):
        requests = []
        def execute(payload):
            requests.append(json.loads(payload))
            return json.dumps({"result": {"tvshows": [{"tvshowid": 7}]}})
        self.assertEqual([{"tvshowid": 7}], KodiLibraryConnector(execute).get_tvshows())
        self.assertEqual("VideoLibrary.GetTVShows", requests[0]["method"])

    def test_kodi_show_creates_franchise_and_provider_season(self):
        class Library:
            def get_tvshows(self):
                return [{"tvshowid": 7, "title": "Show", "uniqueid": {"anilist": "1"}}]
            def get_movies(self):
                return []
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "db.sqlite")
            UserStore(path).initialize()
            store = WatchlistMediaStore(path); store.initialize()
            counts = KodiLibrarySynchronizer(Library(), store).sync()
            self.assertEqual(1, counts["series"])
            self.assertEqual("1", list(store.list_media("season"))[0]["anilist_id"])

if __name__ == "__main__":
    unittest.main()
