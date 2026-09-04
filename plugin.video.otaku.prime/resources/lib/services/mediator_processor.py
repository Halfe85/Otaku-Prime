# -*- coding: utf-8 -*-
"""Simkl-only provider mediation for Prime.

This is the first provider-independent rebuild path.  Watchlist identity may
still contain AniList/MAL/Kitsu IDs, but those providers are observational only:
Simkl is the sole mediation path and TVDB structure returned through Simkl is
the sole authority for TV-series placement.
"""
from __future__ import annotations

from copy import deepcopy

from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)
from resources.lib.services.mediator_simkl_strict import (
    StrictStructuralSimklMediatorEndpoint,
)
from resources.lib.services.mediator_structure import (
    coverage_state,
    placement_rows,
)
from resources.lib.services.mediator_trace import (
    MediatorTrace,
    placement_facts,
    watchlist_input_facts,
)
from resources.lib.service_lifecycle import ServiceWorkHalted


class MediatorProviderConflict(MediatorPlacementError):
    """Compatibility name retained for callers from the Alpha11 API surface."""


class MediatorProcessor:
    """Resolve one watchlist item exclusively through Simkl -> TVDB structure.

    The processor never falls back to AniList, MAL, or Kitsu.  A Simkl failure
    is therefore explicit and terminal for this mediation attempt.  Metadata
    that is genuinely not published yet remains DEFERRED through
    ``MediatorMetadataPending`` rather than being converted into a guessed
    placement.
    """

    def __init__(self, endpoints=None, simkl_client=None, halt_requested=None,
                 network_timeout=30):
        self.halt_requested = halt_requested or (lambda: False)
        supplied = endpoints or {}
        simkl_endpoint = supplied.get("simkl")
        if simkl_endpoint is None:
            simkl_endpoint = StrictStructuralSimklMediatorEndpoint(client=simkl_client)
            if simkl_endpoint.client is None:
                from resources.lib.services.mediator_helper_simkl import SimklMediatorClient
                simkl_endpoint.client = SimklMediatorClient(
                    timeout=network_timeout,
                    halt_requested=self.halt_requested,
                )
        self.endpoints = {"simkl": simkl_endpoint}

    def _checkpoint(self):
        if self.halt_requested():
            raise ServiceWorkHalted("metadata mediation halted for addon shutdown")

    @staticmethod
    def _available(endpoint, item):
        checker = getattr(endpoint, "available", None)
        return bool(checker(item)) if checker else bool(
            item.get("simkl_id") not in (None, "") or
            (item.get("simkl_reference_id") not in (None, "") and
             item.get("special_locator") not in (None, ""))
        )

    @staticmethod
    def _require_tvdb_coordinates(item, placement):
        """Reject any TV placement that is not explicitly located by TVDB."""
        owner = (placement or {}).get("structural_owner") or {}
        library_type = str((placement or {}).get("library_type") or "series")
        if library_type != "movie" and owner.get("tvdb_id") in (None, ""):
            raise MediatorPlacementError(
                "Simkl resolved the media item but did not resolve a TVDB structural series owner"
            )

        bad = []
        for row in placement_rows(placement):
            source = row.get("source_episode_number")
            season = row.get("season_number")
            episode = row.get("episode_number")
            try:
                season_number = int(season)
                episode_number = int(episode)
            except (TypeError, ValueError):
                bad.append(source)
                continue
            if season_number < 0 or episode_number <= 0:
                bad.append(source)
        if bad:
            raise MediatorPlacementError(
                "Simkl placement lacks explicit TVDB coordinates for source episodes {}".format(
                    sorted(set(bad), key=lambda value: str(value))
                )
            )

    @staticmethod
    def _normalize_catalogue_owner(placement):
        """Make TVDB structural ownership the bootstrap Prime TV owner key.

        Alpha11 relation-root IDs are deliberately not allowed to select or
        rename a Prime TV-series in this phase.  The originating tracker IDs
        remain on the watchlist/season/episode records; the TV-series itself is
        anchored by the validated TVDB series ID.
        """
        result = deepcopy(placement or {})
        if str(result.get("library_type") or "series") == "movie":
            return result
        owner = result.get("structural_owner") or {}
        show = result.setdefault("tv_show", {})
        if owner.get("name"):
            show["name"] = owner.get("name")
        show["tvdb_id"] = str(owner.get("tvdb_id"))
        show["simkl_id"] = None
        show["anilist_id"] = None
        show["mal_id"] = None
        show["kitsu_id"] = None
        show["source"] = "simkl_tvdb_structural_owner"
        return result

    def resolve(self, item):
        trace = MediatorTrace((item or {}).get("local_id"))
        trace.info("START", "ITEM_RECEIVED", watchlist_input_facts(item))
        self._checkpoint()

        if str((item or {}).get("identity_resolution_status") or "").upper() == "CONFLICT_EXACT":
            reason = "watchlist exact provider identity is conflicted; Simkl path is not allowed to guess"
            trace.error("IDENTITY", "BLOCKED", watchlist_input_facts(item), reason=reason)
            raise MediatorPlacementError(reason)

        endpoint = self.endpoints["simkl"]
        if not self._available(endpoint, item or {}):
            reason = "watchlist item has no usable Simkl identity or Simkl special reference"
            trace.error("SIMKL_IDENTITY", "UNAVAILABLE", watchlist_input_facts(item), reason=reason)
            raise MediatorPlacementError(reason)

        trace.info(
            "SIMKL_IDENTITY", "REQUEST",
            {
                "simkl_id": (item or {}).get("simkl_id"),
                "simkl_reference_id": (item or {}).get("simkl_reference_id"),
                "special_locator": (item or {}).get("special_locator"),
            },
        )

        try:
            placement = endpoint.resolve(item)
            self._checkpoint()
        except ServiceWorkHalted:
            trace.warning("SIMKL", "HALTED", reason="service shutdown requested")
            raise
        except MediatorMetadataPending as exc:
            trace.warning(
                "SIMKL", "DEFERRED",
                placement_facts(getattr(exc, "placement", None)),
                reason=str(exc),
            )
            raise
        except Exception as exc:
            self._checkpoint()
            reason = "Simkl mediation failed: {}".format(exc)
            trace.error("SIMKL", "FAILED", watchlist_input_facts(item), reason=reason)
            if isinstance(exc, MediatorPlacementError):
                raise MediatorPlacementError(reason) from exc
            raise MediatorPlacementError(reason) from exc

        trace.info("SIMKL", "PLACEMENT_DISCOVERED", placement_facts(placement))

        coverage = coverage_state(item, placement)
        trace.info("COVERAGE", "CHECKED", coverage)
        if not coverage["complete"]:
            reason = "Simkl covered {} of {} source units ({})".format(
                coverage["covered"],
                coverage["expected"] if coverage["expected"] is not None else "unknown",
                coverage["reason"],
            )
            trace.warning("COVERAGE", "DEFERRED", coverage, reason=reason)
            partial = deepcopy(placement)
            partial["provider_attempts"] = [{
                "provider": "simkl", "pending": True, "partial": True,
                "error": reason,
            }]
            raise MediatorMetadataPending(reason, placement=partial)

        try:
            self._require_tvdb_coordinates(item, placement)
            normalized = self._normalize_catalogue_owner(placement)
        except MediatorPlacementError as exc:
            trace.error("TVDB_STRUCTURE", "INVALID", placement_facts(placement), reason=str(exc))
            raise

        trace.info(
            "TVDB_STRUCTURE", "VALIDATED",
            placement_facts(normalized),
            reason="TVDB structural owner and all source episode coordinates are explicit",
        )

        normalized.pop("relation_path", None)
        normalized.pop("franchise_relation_path", None)
        normalized["provider_attempts"] = [{
            "provider": "simkl", "skipped": False, "success": True,
        }]
        trace.info("PLAN", "READY_FOR_CATALOGUE", placement_facts(normalized))
        return normalized
