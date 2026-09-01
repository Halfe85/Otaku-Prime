# -*- coding: utf-8 -*-
"""Final runtime composition for Prime TV and Movies physical libraries."""
from __future__ import annotations

from resources.lib.services.runtime_prime_movie_physical import (
    RuntimePrimeMoviePhysicalSupport,
)
from resources.lib.services.runtime_prime_physical import RuntimePrimePhysicalService


class RuntimePrimePhysicalMoviesService(RuntimePrimePhysicalService):
    """Use the movie runtime that enables recursive folder-per-movie scanning."""

    def __init__(self, *args, artwork_store=None, **kwargs):
        super().__init__(*args, artwork_store=artwork_store, **kwargs)
        self._movies = RuntimePrimeMoviePhysicalSupport(
            self, artwork_store=artwork_store
        )
