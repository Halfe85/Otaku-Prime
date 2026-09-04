# -*- coding: utf-8 -*-
"""Runtime mediator additions for physical, timestamp, and full item tracing."""
from __future__ import annotations

from copy import deepcopy

from resources.lib.services.mediator_helper_simkl import MediatorMetadataPending
from resources.lib.services.mediator_timestamp import MediatorTimestampService
from resources.lib.services.mediator_trace import (
    MediatorTrace,
    placement_facts,
    watchlist_input_facts,
)
from resources.lib.services.mediator_tvshow import TVShowMediatorService
from resources.lib.service_lifecycle import ServiceWorkHalted


class RuntimeTVShowMediatorService(TVShowMediatorService):
    """Run mediation end-to-end while keeping every watchlist item traceable."""

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

    @staticmethod
    def _trace(item, reset=False):
        return MediatorTrace((item or {}).get("local_id"), reset=reset)

    def process_item(self, item):
        """Trace the complete live path around the existing service boundary."""
        trace = self._trace(item, reset=True)
        trace.info(
            "SERVICE", "MEDIATION_BEGIN",
            watchlist_input_facts(item),
            reason="watchdog handed item to Simkl-only mediator",
        )
        try:
            placement = super().process_item(item)
        except ServiceWorkHalted:
            trace.warning("END", "HALTED", reason="service shutdown interrupted mediation")
            raise
        except MediatorMetadataPending as exc:
            trace.warning(
                "END", "DEFERRED",
                placement_facts(getattr(exc, "placement", None)),
                reason=str(exc),
            )
            raise
        except Exception as exc:
            trace.error(
                "END", "FAILED",
                watchlist_input_facts(item),
                reason="{}: {}".format(type(exc).__name__, exc),
            )
            raise

        if (
            self.physical is not None
            and placement.get("library_type") != "movie"
            and placement.get("_prime_owner_id")
        ):
            trace.info(
                "PHYSICAL", "TV_SERIES_PROJECTED",
                {
                    "watchlist_local_id": item.get("local_id"),
                    "prime_series_id": placement.get("_prime_owner_id"),
                    "tvdb_id": (placement.get("structural_owner") or {}).get("tvdb_id"),
                },
                reason="Prime Physical returned without error",
            )

        trace.info(
            "END", "COMPLETE", placement_facts(placement),
            reason="catalogue commit and required physical handoff completed",
        )
        placement.pop("_prime_owner_id", None)
        placement.pop("_prime_season_id", None)
        placement.pop("_prime_multiseason_ids", None)
        return placement

    def _record_deferred(self, item, exc):
        """Persist only ownership/season structure for incomplete provider work."""
        trace = self._trace(item)
        partial = getattr(exc, "placement", None)
        trace.warning(
            "CATALOGUE", "DEFERRED_INPUT",
            placement_facts(partial),
            reason=str(exc),
        )
        if not partial:
            return super()._record_deferred(item, exc)
        structural = deepcopy(partial)
        structural["episodes"] = []
        for component in structural.get("seasons") or []:
            component["episodes"] = []
        replacement = MediatorMetadataPending(str(exc), placement=structural)
        result = super()._record_deferred(item, replacement)
        trace.info(
            "CATALOGUE", "DEFERRED_STRUCTURE_RECORDED",
            placement_facts(structural),
            reason="no guessed episode rows were written",
        )
        return result

    def _persist_placement(self, item, placement, placement_state="COMPLETE"):
        trace = self._trace(item)
        trace.info(
            "CATALOGUE", "WRITE_BEGIN",
            {
                "placement_state": placement_state,
                "placement": placement_facts(placement),
            },
        )
        stored, secondary = super()._persist_placement(
            item, placement, placement_state=placement_state
        )
        is_movie = placement.get("library_type") == "movie"
        is_multiseason_wrapper = bool(placement.get("seasons"))

        if isinstance(stored, dict):
            placement["_prime_owner_id"] = stored.get("local_id")
        if isinstance(secondary, dict):
            placement["_prime_season_id"] = secondary.get("local_id")
        elif isinstance(secondary, list):
            placement["_prime_multiseason_ids"] = [
                row.get("local_id") for row in secondary if isinstance(row, dict)
            ]

        trace.info(
            "CATALOGUE", "WRITE_COMPLETE",
            {
                "placement_state": placement_state,
                "library_type": placement.get("library_type"),
                "prime_owner_id": placement.get("_prime_owner_id"),
                "prime_season_id": placement.get("_prime_season_id"),
                "multi_season_ids": placement.get("_prime_multiseason_ids"),
                "tvdb_owner": (placement.get("structural_owner") or {}).get("tvdb_id"),
                "season_number": (placement.get("season") or {}).get("number"),
            },
            reason="Prime catalogue accepted the placement",
        )

        # Simkl's TVDB owner belongs to this watchlist -> season mapping, not to
        # any tracker relation-root identity.  Record that structural evidence
        # separately after the base catalogue rows exist.
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
                trace.info(
                    "TVDB_STRUCTURE", "EVIDENCE_COMMITTED",
                    {
                        "prime_series_id": placement.get("_prime_owner_id"),
                        "prime_season_id": secondary.get("local_id"),
                        "watchlist_local_id": item.get("local_id"),
                        "structural_owner": owner,
                        "structural_season_number": season_data.get(
                            "structural_season_number", season_data.get("number")
                        ),
                    },
                )

        if (
            self.physical is not None
            and placement_state == "COMPLETE"
            and is_movie
            and stored
        ):
            projector = getattr(self.physical, "project_movie", None)
            if projector:
                trace.info(
                    "PHYSICAL", "MOVIE_PROJECT_BEGIN",
                    {"prime_movie_id": stored.get("local_id")},
                )
                projector(stored["local_id"])
                trace.info(
                    "PHYSICAL", "MOVIE_PROJECTED",
                    {"prime_movie_id": stored.get("local_id")},
                    reason="Prime Physical returned without error",
                )

        # Timestamp work is deliberately queued after the episode identities
        # exist; it is not allowed to alter or delay the structural decision.
        if (
            placement_state == "COMPLETE"
            and not is_movie
            and not is_multiseason_wrapper
            and stored
            and self.timestamp_mediator is not None
        ):
            scheduled = self.timestamp_mediator.schedule_watchlist_item(
                item, series_id=stored["local_id"]
            )
            trace.info(
                "TIMESTAMP", "QUEUED",
                {
                    "prime_series_id": stored.get("local_id"),
                    "result": scheduled,
                },
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
