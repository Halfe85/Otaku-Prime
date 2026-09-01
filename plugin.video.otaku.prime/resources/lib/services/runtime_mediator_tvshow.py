# -*- coding: utf-8 -*-
"""Runtime mediator additions that hand completed movies to Prime Physical."""
from __future__ import annotations

from resources.lib.services.mediator_tvshow import TVShowMediatorService


class RuntimeTVShowMediatorService(TVShowMediatorService):
    """Keep core mediation provider-agnostic while projecting movies at runtime."""

    def _persist_placement(self, item, placement, placement_state="COMPLETE"):
        stored, secondary = super()._persist_placement(
            item, placement, placement_state=placement_state
        )
        if (
            self.physical is not None
            and placement_state == "COMPLETE"
            and placement.get("library_type") == "movie"
            and stored
        ):
            projector = getattr(self.physical, "project_movie", None)
            if projector:
                projector(stored["local_id"])
        return stored, secondary
