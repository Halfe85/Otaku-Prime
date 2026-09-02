# -*- coding: utf-8 -*-
"""Runtime mediator additions for physical and timestamp projection."""
from __future__ import annotations

from resources.lib.services.mediator_timestamp import MediatorTimestampService
from resources.lib.services.mediator_tvshow import TVShowMediatorService


class RuntimeTVShowMediatorService(TVShowMediatorService):
    """Keep provider mediation clean while handing completed runtime projections off."""

    def __init__(self, *args, timestamp_mediator=None, **kwargs):
        network_timeout = kwargs.get("network_timeout", 30)
        super().__init__(*args, **kwargs)
        self.timestamp_mediator = timestamp_mediator or MediatorTimestampService(
            self.catalog_store,
            timeout=max(5, int(network_timeout or 0)),
            halt_requested=lambda: (
                self._stop.is_set()
                or self._stopping.is_set()
                or self._external_halt_requested()
            ),
        )

    def _persist_placement(self, item, placement, placement_state="COMPLETE"):
        stored, secondary = super()._persist_placement(
            item, placement, placement_state=placement_state
        )
        is_movie = placement.get("library_type") == "movie"
        is_multiseason_wrapper = bool(placement.get("seasons"))

        if (
            self.physical is not None
            and placement_state == "COMPLETE"
            and is_movie
            and stored
        ):
            projector = getattr(self.physical, "project_movie", None)
            if projector:
                projector(stored["local_id"])

        # Core mediation has finished writing the episode identities before this
        # handoff. Timestamp work is deliberately queued so AniSkip/TheIntroDB
        # never hold up the main placement worker or Kodi physical projection.
        if (
            placement_state == "COMPLETE"
            and not is_movie
            and not is_multiseason_wrapper
            and stored
            and self.timestamp_mediator is not None
        ):
            self.timestamp_mediator.schedule_watchlist_item(
                item, series_id=stored["local_id"]
            )
        return stored, secondary

    def request_stop(self):
        if self.timestamp_mediator is not None:
            self.timestamp_mediator.request_stop()
        return super().request_stop()

    def stop(self, timeout=35):
        if self.timestamp_mediator is not None:
            self.timestamp_mediator.request_stop()
        stopped = super().stop(timeout=timeout)
        if self.timestamp_mediator is not None:
            self.timestamp_mediator.stop(timeout=min(2, max(0, timeout)))
        return stopped
