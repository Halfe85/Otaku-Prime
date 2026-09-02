from __future__ import annotations

import unittest

from resources.lib.services.mature_artwork import (
    MatureAwareArtworkStore,
    has_hentai_genre,
)


class FakeArtworkStore:
    special_root = "special://masterprofile/otaku-prime/artwork"
    root_path = "/prime/artwork"

    def existing(self, media_type, ids):
        return {
            "poster_url": "/api/artwork/poster",
            "kodi_paths": {
                "poster": self.special_root + "/poster.jpg",
                "fanart": self.special_root + "/fanart.jpg",
                "clearlogo": self.special_root + "/clearlogo.png",
            },
        }

    def kodi_path(self, relative):
        return self.special_root + "/" + str(relative).lstrip("/")


class FakeCatalog:
    def list_series(self):
        return [
            {
                "local_id": "aaaaaa",
                "root_anilist_id": "100",
                "genres_json": '["Action","Hentai"]',
            },
            {
                "local_id": "bbbbbb",
                "root_anilist_id": "200",
                "genres_json": '["Action"]',
            },
        ]

    def list_seasons(self, series_id):
        if str(series_id) == "aaaaaa":
            return [{"anilist_id": "101", "mal_id": "501"}]
        return [{"anilist_id": "201", "mal_id": "601"}]

    def list_movies(self):
        return [
            {
                "local_id": "cccccc",
                "anilist_id": "300",
                "mal_id": "700",
                "genres_json": '["Hentai"]',
            }
        ]


class MatureArtworkTests(unittest.TestCase):
    def test_hentai_classifier_matches_web_library_rule(self):
        self.assertTrue(has_hentai_genre({"genres": ["Fantasy", "Hentai"]}))
        self.assertTrue(has_hentai_genre({"genres_json": '["hentai"]'}))
        self.assertFalse(has_hentai_genre({"genres_json": '["Ecchi"]'}))

    def test_disabled_mature_preference_blurs_sensitive_kodi_paths_only(self):
        proxy = MatureAwareArtworkStore(
            FakeArtworkStore(),
            FakeCatalog(),
            preference_getter=lambda: 0,
            blur_path=lambda path: path + ".blurred",
        )

        result = proxy.existing("tvshows", {"anilist": "100"})

        self.assertEqual("/api/artwork/poster", result["poster_url"])
        self.assertTrue(result["kodi_paths"]["poster"].endswith(".blurred"))
        self.assertTrue(result["kodi_paths"]["fanart"].endswith(".blurred"))
        self.assertEqual(
            "special://masterprofile/otaku-prime/artwork/clearlogo.png",
            result["kodi_paths"]["clearlogo"],
        )

    def test_enabled_mature_preference_restores_original_kodi_paths(self):
        proxy = MatureAwareArtworkStore(
            FakeArtworkStore(),
            FakeCatalog(),
            preference_getter=lambda: 1,
            blur_path=lambda path: path + ".blurred",
        )

        result = proxy.existing("tvshows", {"mal": "501"})

        self.assertEqual(
            "special://masterprofile/otaku-prime/artwork/poster.jpg",
            result["kodi_paths"]["poster"],
        )

    def test_non_hentai_title_is_never_blurred(self):
        proxy = MatureAwareArtworkStore(
            FakeArtworkStore(),
            FakeCatalog(),
            preference_getter=lambda: 0,
            blur_path=lambda path: path + ".blurred",
        )

        result = proxy.existing("tvshows", {"anilist": "200"})

        self.assertEqual(
            "special://masterprofile/otaku-prime/artwork/poster.jpg",
            result["kodi_paths"]["poster"],
        )

    def test_movie_uses_same_mature_switch_and_policy(self):
        proxy = MatureAwareArtworkStore(
            FakeArtworkStore(),
            FakeCatalog(),
            preference_getter=lambda: 0,
            blur_path=lambda path: path + ".blurred",
        )

        result = proxy.existing("movies", {"mal": "700"})

        self.assertTrue(result["kodi_paths"]["poster"].endswith(".blurred"))
        self.assertEqual(["aaaaaa"], proxy.mature_series_ids())
        self.assertEqual(["cccccc"], proxy.mature_movie_ids())

    def test_blur_failure_fails_closed_instead_of_exposing_original(self):
        def fail(_path):
            raise RuntimeError("blur failed")

        proxy = MatureAwareArtworkStore(
            FakeArtworkStore(),
            FakeCatalog(),
            preference_getter=lambda: 0,
            blur_path=fail,
        )

        result = proxy.existing("tvshows", {"anilist": "100"})

        self.assertNotIn("poster", result["kodi_paths"])
        self.assertNotIn("fanart", result["kodi_paths"])
        self.assertIn("clearlogo", result["kodi_paths"])


if __name__ == "__main__":
    unittest.main()
