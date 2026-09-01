from __future__ import annotations

import unittest
from unittest.mock import patch

from resources.lib.services.mediator_tvshow import TVShowMediatorService
from resources.lib.services.runtime_mediator_tvshow import RuntimeTVShowMediatorService


class FakePhysical:
    def __init__(self):
        self.movies = []

    def project_movie(self, movie_id):
        self.movies.append(str(movie_id))
        return {"movie_id": str(movie_id)}


class RuntimeMediatorMoviePhysicalTests(unittest.TestCase):
    def test_completed_movie_is_handed_to_prime_physical(self):
        service = RuntimeTVShowMediatorService.__new__(RuntimeTVShowMediatorService)
        service.physical = FakePhysical()
        placement = {"library_type": "movie"}
        stored = {"local_id": "123abc"}

        with patch.object(
            TVShowMediatorService,
            "_persist_placement",
            return_value=(stored, None),
        ):
            result = service._persist_placement(
                {"local_id": "watchlist-id"},
                placement,
                placement_state="COMPLETE",
            )

        self.assertEqual((stored, None), result)
        self.assertEqual(["123abc"], service.physical.movies)

    def test_structure_only_movie_does_not_create_physical_files(self):
        service = RuntimeTVShowMediatorService.__new__(RuntimeTVShowMediatorService)
        service.physical = FakePhysical()
        stored = {"local_id": "123abc"}

        with patch.object(
            TVShowMediatorService,
            "_persist_placement",
            return_value=(stored, None),
        ):
            service._persist_placement(
                {"local_id": "watchlist-id"},
                {"library_type": "movie"},
                placement_state="STRUCTURE_ONLY",
            )

        self.assertEqual([], service.physical.movies)


if __name__ == "__main__":
    unittest.main()
