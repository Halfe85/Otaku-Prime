from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.watchlist_release import WatchlistReleaseManager, release_epoch


class SegmentFactory:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return "{:06x}".format(self.value)


class LibraryCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "users.sqlite")
        self.watchlist = WatchlistItemStore(self.path)
        self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist", [{
            "provider_item_id": "100",
            "ids": {"anilist": "100", "mal": "200", "kitsu": "300", "simkl": "400"},
            "english_name": "Example Season",
            "romaji_name": "Example Season",
            "list_status": "CURRENT",
            "provider_status": "CURRENT",
            "progress": 1,
            "episode_count": 2,
            "media_format": "TV",
            "release_date": "2030-01-01",
            "provider_updated_at": "2030-01-02T00:00:00Z",
            "raw": {},
        }])
        self.watchlist.finalize_merge()
        self.item = self.watchlist.list_all()[0]

        self.catalog = CatalogStore(self.path, SegmentFactory())
        self.catalog.initialize()
        self.release = WatchlistReleaseManager(self.watchlist)
        self.release.initialize()

        self.series = self.catalog.get_or_create_series(
            english_name="Example Series",
            romaji_name="Example Series Romaji",
            root_simkl_id="399",
            root_anilist_id="99",
            tvdb_id="999",
            source_provider="simkl",
            source_media_format="TV",
            publish_year=2030,
            overview="A mediated series overview.",
            runtime_minutes=24,
            air_status="airing",
            poster_url="https://img.example/poster.webp",
            fanart_url="https://img.example/fanart.webp",
            clearlogo_url="https://img.example/logo.webp",
            banner_url="https://img.example/banner.webp",
            genres=["Action","Fantasy"],
            themes=["Isekai","Magic"],
            age_rating="R+",
            mature=True,
        )
        self.catalog.replace_media_credits([
            {"person":{"anilist_id":"501","name":"Actor One","trivia":"Known fact",
                       "date_of_birth":"1980-01-02","age":50,"image_url":"https://img/staff-1.jpg"},
             "character":{"anilist_id":"601","name":"Hero","trivia":"Main character",
                          "image_url":"https://img/character-1.jpg"},"sort_order":0},
            {"person":{"anilist_id":"502","name":"Actor Two"},
             "character":{"anilist_id":"602","name":"Rival"},"sort_order":1},
            {"person":{"anilist_id":"503","name":"Series Director","trivia":"Directed it",
                       "image_url":"https://img/director.jpg"},
             "character":{},"credit_type":"Director","sort_order":2},
        ], series_id=self.series["local_id"], source_provider="anilist")
        self.season = self.catalog.add_watchlist_season(
            self.series["local_id"], self.item, season_number=1,
            provider_path="simkl", placement_source="mapped_tvdb_seasons",
            first_episode=1, last_episode=2,
        )
        self.ep1 = self.catalog.add_episode(
            self.season["local_id"], 1, source_episode_number=1,
            simkl_id="401", title="Arrival", overview="Episode one overview.",
            runtime_minutes=24, release_date="2030-01-01",
        )
        self.ep2 = self.catalog.add_episode(
            self.season["local_id"], 2, source_episode_number=2,
            simkl_id="402", title="Return", overview="Episode two overview.",
            runtime_minutes=25, release_date="2030-01-08",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_library_tile_aggregates_series_counts_and_next_release(self):
        self.release.refresh_due(
            now_epoch=release_epoch("2030-01-05T12:00:00Z"), force=True
        )
        rows = self.catalog.library_series()
        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertEqual(self.series["local_id"], row["local_id"])
        self.assertEqual("Example Series", row["title"])
        self.assertEqual(2030, row["publish_year"])
        self.assertEqual(1, row["season_count"])
        self.assertEqual(2, row["episode_count"])
        self.assertEqual(2, row["next_episode_number"])
        self.assertEqual("2030-01-08", row["next_episode_release_date"])
        self.assertEqual("RUNNING", row["library_status"])

    def test_series_detail_contains_cast_seasons_and_episode_metadata(self):
        detail = self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual("A mediated series overview.", detail["overview"])
        self.assertEqual(24, detail["runtime_minutes"])
        self.assertEqual(3, len(detail["cast"]))
        self.assertEqual(("Actor One", "Hero"), (
            detail["cast"][0]["person_name"], detail["cast"][0]["character_name"]
        ))
        self.assertEqual("Known fact",detail["cast"][0]["person"]["trivia"])
        self.assertEqual("1980-01-02",detail["cast"][0]["person"]["date_of_birth"])
        self.assertEqual("https://img/character-1.jpg",
                         detail["cast"][0]["character"]["image_url"])
        self.assertEqual(1,len(detail["staff"]))
        self.assertEqual(2,len(detail["characters"]))
        hero=next(row for row in detail["characters"] if row["name"]=="Hero")
        director=detail["staff"][0]
        self.assertEqual(["Actor One"],[row["name"] for row in hero["staff"]])
        self.assertEqual(["series"],[row["scope"] for row in hero["media_links"]])
        self.assertEqual("Series Director",director["name"])
        self.assertEqual(["Director"],[row["credit_type"] for row in director["roles"]])
        self.assertEqual(["series"],[row["scope"] for row in director["media_links"]])
        db=sqlite3.connect(self.path)
        try:
            self.assertEqual(3,db.execute("SELECT COUNT(*) FROM staff").fetchone()[0])
            self.assertEqual(2,db.execute("SELECT COUNT(*) FROM characters").fetchone()[0])
            self.assertEqual(2,db.execute(
                "SELECT COUNT(*) FROM staff_character_links").fetchone()[0])
            self.assertEqual(2,db.execute(
                "SELECT COUNT(*) FROM character_media_links WHERE related_series_id=?",
                (self.series["local_id"],)).fetchone()[0])
            self.assertEqual(1,db.execute(
                "SELECT COUNT(*) FROM staff_media_links WHERE related_series_id=?",
                (self.series["local_id"],)).fetchone()[0])
        finally:
            db.close()
        self.assertEqual(1, len(detail["seasons"]))
        episodes = detail["seasons"][0]["episodes"]
        self.assertEqual(2, len(episodes))
        self.assertEqual("Arrival", episodes[0]["title"])
        self.assertEqual("Episode one overview.", episodes[0]["overview"])
        self.assertEqual(24, episodes[0]["runtime_minutes"])
        self.assertEqual("Return", episodes[1]["title"])
        self.assertEqual(25, episodes[1]["runtime_minutes"])

    def test_metadata_refresh_preserves_prime_ids(self):
        same_series = self.catalog.get_or_create_series(
            english_name="Example Series",
            root_simkl_id="399",
            tvdb_id="999",
            publish_year=2030,
            overview="Updated series overview.",
            runtime_minutes=25,
            air_status="finished",
            genres=["Drama"],
            themes=["Coming of Age"],
        )
        same_episode = self.catalog.add_episode(
            self.season["local_id"], 1, source_episode_number=1,
            simkl_id="401", title="Arrival Updated",
            overview="Updated episode overview.", runtime_minutes=26,
            release_date="2030-01-01",
        )
        self.assertEqual(self.series["local_id"], same_series["local_id"])
        self.assertEqual(self.ep1["local_id"], same_episode["local_id"])
        self.assertEqual("Updated series overview.", same_series["overview"])
        self.assertEqual("Arrival Updated", same_episode["title"])
        detail=self.catalog.library_series_detail(same_series["local_id"])
        self.assertEqual(["Action","Fantasy","Drama"],detail["genres"])
        self.assertEqual(["Isekai","Magic","Coming of Age"],detail["themes"])

    def test_watchlist_progress_projects_sequential_source_episode_state(self):
        first=self.catalog.project_watchlist_progress(self.item["local_id"],1)
        detail=self.catalog.library_series_detail(self.series["local_id"])
        episodes=detail["seasons"][0]["episodes"]

        self.assertEqual((2,1),(first["episode_count"],first["watched_count"]))
        self.assertEqual([1,0],[row["watch_status"] for row in episodes])

        second=self.catalog.project_watchlist_progress(self.item["local_id"],2)
        detail=self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual(2,second["watched_count"])
        self.assertEqual([1,1],[
            row["watch_status"] for row in detail["seasons"][0]["episodes"]])

        # A later metadata refresh must not reset a projected watch state.
        refreshed=self.catalog.add_episode(
            self.season["local_id"],2,source_episode_number=2,title="Return refreshed")
        self.assertEqual(1,refreshed["watch_status"])

        context=self.catalog.episode_watch_context(self.ep2["local_id"])
        self.assertEqual(self.item["local_id"],context["watchlist_local_id"])
        self.assertEqual(2,context["source_episode_number"])

    def test_existing_season_zero_titles_are_backfilled_without_overwriting_metadata(self):
        db=sqlite3.connect(self.path)
        try:
            db.execute("UPDATE seasons SET season_number=0 WHERE local_id=?",
                       (self.season["local_id"],))
            db.execute("UPDATE episodes SET title=NULL WHERE local_id=?",
                       (self.ep1["local_id"],))
            db.execute("UPDATE episodes SET title='Provider title' WHERE local_id=?",
                       (self.ep2["local_id"],))
            db.commit()
        finally:
            db.close()

        CatalogStore(self.path,SegmentFactory()).initialize()
        rows=self.catalog.list_episodes(self.season["local_id"])

        self.assertEqual(["Example Season","Provider title"],
                         [row["title"] for row in rows])

    def test_season_zero_is_shared_by_distinct_watchlist_items(self):
        self.watchlist.replace_provider_snapshot("mal",[{
            "provider_item_id":"998","ids":{"mal":"998"},
            "english_name":"First Special","list_status":"PLANNING",
            "progress":0,"episode_count":1,"media_format":"OAD","raw":{},
        }])
        self.watchlist.replace_provider_snapshot("kitsu",[{
            "provider_item_id":"999","ids":{"kitsu":"999"},
            "english_name":"Second Special","list_status":"PLANNING",
            "progress":0,"episode_count":1,"media_format":"OAD","raw":{},
        }])
        self.watchlist.finalize_merge()
        first_item=next(row for row in self.watchlist.list_all()
                        if row["mal_id"]=="998")
        second=next(row for row in self.watchlist.list_all()
                    if row["kitsu_id"]=="999")
        series=self.catalog.get_or_create_series(
            english_name="Shared Specials",root_anilist_id="shared-special-root")
        first_season=self.catalog.add_watchlist_season(
            series["local_id"],first_item,season_number=0,
            provider_path="anilist",placement_source="special")
        second_season=self.catalog.add_watchlist_season(
            series["local_id"],second,season_number=0,
            provider_path="mal",placement_source="special")

        first=self.catalog.add_episode(
            first_season["local_id"],1,source_episode_number=1,
            watchlist_local_id=first_item["local_id"],title="First Special")
        second_episode=self.catalog.add_episode(
            second_season["local_id"],1,source_episode_number=1,
            watchlist_local_id=second["local_id"],title="Second Special")

        self.assertEqual(first_season["local_id"],second_season["local_id"])
        self.assertEqual((1,2),(first["episode_number"],second_episode["episode_number"]))
        self.assertEqual(1,len(self.catalog.list_seasons(series["local_id"])))
        self.assertEqual({first_item["local_id"],second["local_id"]},
                         {row["watchlist_local_id"] for row in
                          self.catalog.list_episodes(first_season["local_id"])})

    def test_old_special_franchise_projection_is_removed_and_requeued(self):
        db=sqlite3.connect(self.path)
        try:
            db.execute("UPDATE tv_series SET source_media_format='MOVIE' WHERE local_id=?",
                       (self.series["local_id"],))
            db.execute("""UPDATE watchlist_items SET added_to_library=1,
              mediator_ready=0,mediator_status='COMPLETE' WHERE local_id=?""",
                       (self.item["local_id"],))
            db.execute("""UPDATE prime_catalog_state SET value='old-model'
              WHERE key='projection_revision'""")
            db.commit()
        finally:
            db.close()

        with self.assertLogs("otaku_prime.database-catalog",level="WARNING"):
            CatalogStore(self.path,SegmentFactory()).initialize()

        self.assertEqual([],self.catalog.list_series())
        repaired=self.watchlist.item(self.item["local_id"])
        self.assertEqual((0,1,"PARTIAL"),(
            repaired["added_to_library"],repaired["mediator_ready"],
            repaired["mediator_status"]))
        self.assertEqual("Franchise ownership rebuild required",
                         repaired["mediator_error"])

    def test_series_artwork_urls_are_projected_to_tiles_and_detail(self):
        tile=self.catalog.library_series()[0]
        detail=self.catalog.library_series_detail(self.series["local_id"])

        self.assertEqual("https://img.example/poster.webp",tile["poster_url"])
        self.assertEqual("https://img.example/fanart.webp",tile["fanart_url"])
        self.assertEqual("https://img.example/logo.webp",tile["clearlogo_url"])
        self.assertEqual("https://img.example/banner.webp",detail["banner_url"])

    def test_legacy_logo_url_is_upgraded_to_explicit_clearlogo_url(self):
        other=os.path.join(self.tmp.name,"legacy-artwork.sqlite")
        with sqlite3.connect(other) as db:
            db.execute("""CREATE TABLE tv_series(
              local_id TEXT PRIMARY KEY,english_name TEXT,romaji_name TEXT,
              root_simkl_id TEXT UNIQUE,logo_url TEXT,
              created_at TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            db.execute("""INSERT INTO tv_series(
              local_id,english_name,logo_url) VALUES(?,?,?)""",
              ("abcdef","Legacy Series","https://img.example/legacy-logo.webp"))

        CatalogStore(other,SegmentFactory()).initialize()

        with sqlite3.connect(other) as db:
            row=db.execute("""SELECT poster_url,clearlogo_url,banner_url
              FROM tv_series WHERE local_id='abcdef'""").fetchone()
        self.assertEqual(
            (None,"https://img.example/legacy-logo.webp",None),row)

    def test_series_classification_is_projected_as_lists_and_binary_mature(self):
        tile=self.catalog.library_series()[0]
        detail=self.catalog.library_series_detail(self.series["local_id"])

        self.assertEqual(["Action","Fantasy"],tile["genres"])
        self.assertEqual(["Isekai","Magic"],detail["themes"])
        self.assertEqual("R+",detail["age_rating"])
        self.assertEqual(1,detail["mature"])
        self.assertNotIn("genres_json",tile)

    def test_missing_cast_metadata_does_not_delete_existing_cast(self):
        self.catalog.replace_media_credits(
            None,series_id=self.series["local_id"],source_provider="other")
        detail = self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual(3, len(detail["cast"]))

    def test_empty_provider_cast_does_not_erase_existing_cast(self):
        self.catalog.replace_media_credits(
            [],series_id=self.series["local_id"],source_provider="simkl")
        detail = self.catalog.library_series_detail(self.series["local_id"])
        self.assertEqual(3,len(detail["cast"]))

    def test_credits_can_be_scoped_to_season_and_episode(self):
        season_credit={"person_name":"Season Actor","character_name":"Season Character"}
        episode_credit={"person_name":"Guest Actor","character_name":"Guest Character"}
        self.catalog.replace_media_credits(
            [season_credit],season_id=self.season["local_id"],source_provider="simkl")
        self.catalog.replace_media_credits(
            [episode_credit],episode_id=self.ep1["local_id"],source_provider="simkl")

        detail=self.catalog.library_series_detail(self.series["local_id"])
        season=detail["seasons"][0]
        self.assertEqual("Season Character",season["cast"][0]["character"]["name"])
        self.assertEqual("Guest Actor",season["episodes"][0]["cast"][0]["person"]["name"])
        self.assertEqual([],season["episodes"][1]["cast"])
        self.assertEqual(1,len(detail["staff"]))
        self.assertEqual(4,len(detail["characters"]))
        season_character=next(
            row for row in detail["characters"] if row["name"]=="Season Character")
        guest_character=next(
            row for row in detail["characters"] if row["name"]=="Guest Character")
        self.assertEqual("season",season_character["media_links"][0]["scope"])
        self.assertEqual(1,season_character["media_links"][0]["season_number"])
        self.assertEqual("episode",guest_character["media_links"][0]["scope"])
        self.assertEqual(1,guest_character["media_links"][0]["episode_number"])

    def test_character_without_staff_is_still_linked_to_media(self):
        self.catalog.replace_media_credits([{
            "character":{"anilist_id":"999","name":"Silent Character",
                         "trivia":"No voice actor published yet"},"person":{}
        }],series_id=self.series["local_id"],source_provider="anilist")

        detail=self.catalog.library_series_detail(self.series["local_id"])

        self.assertEqual([],detail["staff"])
        self.assertEqual("Silent Character",detail["characters"][0]["name"])
        self.assertEqual({},detail["cast"][0]["person"])

    def test_non_anilist_staff_and_character_ids_are_preserved(self):
        self.catalog.replace_media_credits([{
            "source_provider":"mal",
            "person":{"provider_id":"700","name":"MAL Actor"},
            "character":{"provider_id":"800","name":"MAL Character"},
        }],season_id=self.season["local_id"],source_provider="mal")

        detail=self.catalog.library_series_detail(self.series["local_id"])
        character=next(row for row in detail["characters"]
                       if row["name"]=="MAL Character")

        self.assertEqual("800",character["mal_id"])
        self.assertEqual("700",character["staff"][0]["mal_id"])

    def test_legacy_flat_cast_table_is_discarded_and_not_migrated(self):
        other=os.path.join(self.tmp.name,"legacy.sqlite")
        legacy_watchlist=WatchlistItemStore(other); legacy_watchlist.initialize()
        legacy_watchlist.replace_provider_snapshot("anilist",[{
            "provider_item_id":"1","ids":{"anilist":"1"},"english_name":"Legacy",
            "list_status":"PLANNING","progress":0,"raw":{}}])
        local_id=legacy_watchlist.list_all()[0]["local_id"]
        db=sqlite3.connect(other)
        try:
            db.execute("CREATE TABLE series_cast(related_series_id TEXT,person_name TEXT)")
            db.execute("INSERT INTO series_cast VALUES('old','Discard Me')")
            db.commit()
        finally:
            db.close()
        rebuilt=CatalogStore(other,SegmentFactory()); rebuilt.initialize()
        db=sqlite3.connect(other)
        try:
            tables={row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("series_cast",tables)
            self.assertEqual(0,db.execute("SELECT COUNT(*) FROM staff").fetchone()[0])
            state=db.execute("SELECT mediator_ready,added_to_library FROM watchlist_items "
                             "WHERE local_id=?",(local_id,)).fetchone()
            self.assertEqual((1,0),state)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
