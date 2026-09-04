# -*- coding: utf-8 -*-
"""Runtime mediator additions for physical and timestamp projection."""
from __future__ import annotations

from copy import deepcopy

from resources.lib.services.mediator_helper_simkl import MediatorMetadataPending
from resources.lib.services.mediator_timestamp import MediatorTimestampService
from resources.lib.services.mediator_tvshow import TVShowMediatorService


class RuntimeTVShowMediatorService(TVShowMediatorService):
    """Keep provider mediation clean while handing completed runtime projections off."""

    def __init__(self, *args, timestamp_mediator=None, **kwargs):
        network_timeout = kwargs.get("network_timeout", 30)
        timestamp_timeout = kwargs.pop(
            "timestamp_timeout", max(5, int(network_timeout or 0))
        )
        super().__init__(*args, **kwargs)
        self.timestamp_mediator = timestamp_mediator or MediatorTimestampService(
            self.catalog_store,
            timeout=max(1, int(timestamp_timeout or 0)),
            halt_requested=lambda: (
                self._stop.is_set()
                or self._stopping.is_set()
                or self._external_halt_requested()
            ),
        )

    def _record_deferred(self, item, exc):
        """Persist only ownership/season structure for incomplete provider work."""
        partial = getattr(exc, "placement", None)
        if not partial:
            return super()._record_deferred(item, exc)
        structural = deepcopy(partial)
        structural["episodes"] = []
        for component in structural.get("seasons") or []:
            component["episodes"] = []
        replacement = MediatorMetadataPending(str(exc), placement=structural)
        return super()._record_deferred(item, replacement)

    def _persist_placement(self, item, placement, placement_state="COMPLETE"):
        stored, secondary = super()._persist_placement(
            item, placement, placement_state=placement_state
        )
        is_movie = placement.get("library_type") == "movie"
        is_multiseason_wrapper = bool(placement.get("seasons"))

        # Simkl's TVDB owner belongs to this watchlist -> season mapping, not to
        # the parent franchise.  The base mediator has already persisted the
        # franchise and season by this point, so record the structural evidence
        # separately without allowing it to rename/re-root the parent.
        if not is_movie and not is_multiseason_wrapper and secondary:
            setter = getattr(self.catalog_store, "set_watchlist_structural_owner", None)
            owner = placement.get("structural_owner") or None
            if setter and owner:
                season_data = placement.get("season") or {}
                setter(
                    secondary["local_id"],
                    item["local_id"],
                    owner,
                    structural_season_number=season_data.get(
                        "structural_season_number", season_data.get("number")
                    ),
                    source_provider=placement.get("provider_path"),
                )

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
