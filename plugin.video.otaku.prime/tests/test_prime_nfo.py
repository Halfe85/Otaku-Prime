import os
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree

from resources.lib.services.prime_nfo import PrimeNfoWriter


class FakeCatalog:
    def __init__(self):
        self.series = {
            "local_id": "abcdef",
            "english_name": "Frieren: Beyond Journey's End",
            "romaji_name": "Sousou no Frieren",
            "root_anilist_id": "154587",
            "root_simkl_id": "2222222",
            "tvdb_id": "424536",
            "publish_year": 2023,
            "overview": "After the party defeats the Demon King, Frieren travels on.",
            "age_rating": "TV-14",
            "genres": ["Adventure", "Fantasy"],
            "themes": ["Magic"],
        }
        self.seasons = [{
            "local_id": "abcdef000001",
            "related_series_id": "abcdef",
            "season_number": 1,
            "release_date": "2023-09-29",
            "mal_id": "52991",
            "kitsu_id": "47130",
        }]
        self.episodes = [{
            "local_id": "abcdef000001000001",
            "related_season_id": "abcdef000001",
            "episode_number": 1,
            "source_episode_number": 1,
            "title": "The Journey's End",
            "overview": "The heroes return after defeating the Demon King.",
            "runtime_minutes": 25,
            "watch_status": 1,
            "release_date": "2023-09-29",
            "anilist_id": "154587",
            "mal_id": "52991",
        }, {
            "local_id": "abcdef000001000002",
            "related_season_id": "abcdef000001",
            "episode_number": 2,
            "source_episode_number": 2,
            "title": "It Didn't Have to Be Magic...",
            "overview": "A later episode.",
            "runtime_minutes": 24,
            "watch_status": 0,
            "release_date": "2030-01-01",
            "anilist_id": "154587",
            "mal_id": "52991",
        }]

    def get_series(self, series_id):
        return dict(self.series) if str(series_id) == "abcdef" else None

    def list_series(self):
        return [dict(self.series)]

    def list_seasons(self, series_id):
        return [dict(row) for row in self.seasons if row["related_series_id"] == series_id]

    def list_episodes(self, season_id):
        return [dict(row) for row in self.episodes if row["related_season_id"] == season_id]


class FakeArtworkStore:
    def existing(self, media_type, ids):
        if media_type == "tvshows":
            self.last_series_ids = dict(ids)
            return {"kodi_paths": {
                "poster": "special://masterprofile/otaku-prime/artwork/tvshows/anilist-154587/poster.jpg",
                "fanart": "special://masterprofile/otaku-prime/artwork/tvshows/anilist-154587/fanart.jpg",
                "clearlogo": "special://masterprofile/otaku-prime/artwork/tvshows/anilist-154587/clearlogo.png",
                "banner": "special://masterprofile/otaku-prime/artwork/tvshows/anilist-154587/banner.jpg",
            }}
        if media_type == "episodes":
            return {"kodi_paths": {
                "thumb": "special://masterprofile/otaku-prime/artwork/episodes/anilist-154587/thumb.jpg",
            }}
        return {}


class PrimeNfoTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.catalog = FakeCatalog()
        self.artwork = FakeArtworkStore()
        self.series_directory = os.path.join(
            self.temporary.name, "Frieren - Beyond Journey's End 2023"
        )
        self.season_directory = os.path.join(self.series_directory, "Season 01")
        os.makedirs(self.season_directory)
        self.episode_stem = "Frieren - Beyond Journey's End - S01E01"
        with open(os.path.join(self.season_directory, self.episode_stem + ".strm"), "wb"):
            pass

    def tearDown(self):
        self.temporary.cleanup()

    def test_writes_tvshow_and_adjacent_episode_nfo_with_art_paths(self):
        writer = PrimeNfoWriter(self.catalog, artwork_store=self.artwork)
        result = writer.write_series(
            "abcdef", self.series_directory, now_epoch=1767225600
        )

        tvshow_path = os.path.join(self.series_directory, "tvshow.nfo")
        episode_path = os.path.join(
            self.season_directory, self.episode_stem + ".nfo"
        )
        self.assertTrue(os.path.isfile(tvshow_path))
        self.assertTrue(os.path.isfile(episode_path))
        self.assertEqual(2, result["written"])
        self.assertEqual(1, result["episodes"])

        tvshow = ElementTree.parse(tvshow_path).getroot()
        self.assertEqual("Frieren: Beyond Journey's End", tvshow.findtext("title"))
        self.assertEqual("Sousou no Frieren", tvshow.findtext("originaltitle"))
        self.assertEqual("2023", tvshow.findtext("year"))
        self.assertEqual("2023-09-29", tvshow.findtext("premiered"))
        self.assertEqual(["Adventure", "Fantasy"], [
            node.text for node in tvshow.findall("genre")
        ])
        self.assertEqual("Magic", tvshow.findtext("tag"))
        self.assertEqual("abcdef", tvshow.find("uniqueid[@type='prime']").text)
        self.assertEqual("true", tvshow.find("uniqueid[@type='prime']").get("default"))
        self.assertEqual("154587", tvshow.find("uniqueid[@type='anilist']").text)
        self.assertEqual(
            "special://masterprofile/otaku-prime/artwork/tvshows/anilist-154587/poster.jpg",
            tvshow.find("thumb[@aspect='poster']").text,
        )
        self.assertEqual(
            "special://masterprofile/otaku-prime/artwork/tvshows/anilist-154587/fanart.jpg",
            tvshow.find("fanart/thumb").text,
        )
        self.assertEqual("52991", self.artwork.last_series_ids["mal"])
        self.assertEqual("47130", self.artwork.last_series_ids["kitsu"])

        episode = ElementTree.parse(episode_path).getroot()
        self.assertEqual("The Journey's End", episode.findtext("title"))
        self.assertEqual("Frieren: Beyond Journey's End", episode.findtext("showtitle"))
        self.assertEqual("1", episode.findtext("season"))
        self.assertEqual("1", episode.findtext("episode"))
        self.assertEqual("25", episode.findtext("runtime"))
        self.assertEqual("2023-09-29", episode.findtext("aired"))
        self.assertEqual("1", episode.findtext("playcount"))
        self.assertEqual(
            "special://masterprofile/otaku-prime/artwork/episodes/anilist-154587/thumb.jpg",
            episode.find("thumb[@aspect='thumb']").text,
        )

    def test_future_episode_without_strm_does_not_get_nfo(self):
        writer = PrimeNfoWriter(self.catalog, artwork_store=self.artwork)
        writer.write_series("abcdef", self.series_directory, now_epoch=1767225600)
        future_nfo = os.path.join(
            self.season_directory,
            "Frieren - Beyond Journey's End - S01E02.nfo",
        )
        self.assertFalse(os.path.exists(future_nfo))

    def test_nfo_projection_is_idempotent_when_metadata_did_not_change(self):
        writer = PrimeNfoWriter(self.catalog, artwork_store=self.artwork)
        first = writer.write_series("abcdef", self.series_directory, now_epoch=1767225600)
        second = writer.write_series("abcdef", self.series_directory, now_epoch=1767225600)
        self.assertEqual(2, first["written"])
        self.assertEqual(0, second["written"])
        self.assertEqual(2, second["unchanged"])


if __name__ == "__main__":
    unittest.main()
