# -*- coding: utf-8 -*-
"""Simkl-first identity watchdog used before Prime mediation.

The watchdog owns provider-ID acquisition.  The mediator never searches for or
repairs identities.  A row is mediator-ready only when the watchdog has either
an exact Simkl anime identity or an exact Simkl special reference + locator.
"""
from __future__ import annotations

import json

from resources.lib.logging_config import get_logger
from resources.lib.service_lifecycle import ServiceWorkHalted
from resources.lib.services.watchlist_identity import (
    IdentityMappingConflict,
    KitsuIdentityClient,
    PROVIDERS,
    ProviderIdentityClient,
    SimklIdentityClient,
    WatchlistIdentityEnrichmentService,
)


LOGGER = get_logger(__name__)


def _trace(local_id, stage, event, facts=None, reason=None, level="info"):
    parts = [
        "WATCHDOG[{}]".format(str(local_id or "UNKNOWN")),
        "stage={}".format(stage),
        "event={}".format(event),
    ]
    if reason not in (None, ""):
        parts.append("reason={}".format(json.dumps(str(reason), ensure_ascii=False)))
    if facts is not None:
        parts.append("facts={}".format(json.dumps(
            facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )))
    getattr(LOGGER, level)(" ".join(parts))


class SimklFirstIdentityClient(SimklIdentityClient):
    """Resolve Simkl from every exact tracker path before declaring NOT_FOUND."""

    @staticmethod
    def _candidate_id(row):
        ids = (row or {}).get("ids") or {}
        value = ids.get("simkl") or ids.get("simkl_id")
        return str(value) if value not in (None, "") else None

    def _search_candidate_ids(self, known):
        values = []
        evidence = []
        for provider in ("anilist", "mal", "kitsu"):
            value = known.get(provider)
            if value in (None, ""):
                continue
            rows = self._search(provider, value)
            matches = []
            for row in rows or []:
                if str(row.get("type") or "").lower() != "anime":
                    continue
                simkl_id = self._candidate_id(row)
                if not simkl_id:
                    continue
                matches.append(simkl_id)
                if simkl_id not in values:
                    values.append(simkl_id)
            evidence.append({
                "provider": provider,
                "provider_id": str(value),
                "simkl_candidates": matches,
            })
        return values, evidence

    def _special_reference(self, item, simkl_reference_id):
        """Accept only exact external-ID evidence for a referenced special."""
        exact_coordinates = []
        evidence = []
        for row in self._episodes(simkl_reference_id):
            coordinate = self._row_coordinate(row)
            if not coordinate or coordinate[0] != 0:
                continue
            if str(row.get("type") or "").lower() not in ("special", "episode", ""):
                continue
            row_ids = row.get("ids") or {}
            matched = []
            for provider in ("anilist", "mal", "kitsu"):
                known = item.get(provider + "_id")
                remote = row_ids.get(provider)
                if (
                    known not in (None, "")
                    and remote not in (None, "")
                    and str(known) == str(remote)
                ):
                    matched.append(provider)
            if matched:
                exact_coordinates.append(coordinate)
                evidence.append({
                    "coordinate": "S{:02d}E{:02d}".format(*coordinate),
                    "matched_providers": matched,
                })

        exact_coordinates = sorted(set(exact_coordinates))
        if not exact_coordinates:
            return None
        seasons = {coordinate[0] for coordinate in exact_coordinates}
        numbers = [coordinate[1] for coordinate in exact_coordinates]
        if len(seasons) != 1 or numbers != list(range(numbers[0], numbers[-1] + 1)):
            return None
        season = exact_coordinates[0][0]
        locator = "S{:02d}E{:02d}".format(season, numbers[0])
        if len(numbers) > 1:
            locator += "-E{:02d}".format(numbers[-1])
        return {
            "_simkl_reference_id": str(simkl_reference_id),
            "_special_locator": locator,
            "_special_identity_evidence": evidence,
        }

    def resolve(self, item):
        local_id = (item or {}).get("local_id")
        known = {name: item.get(name + "_id") for name in PROVIDERS}
        _trace(local_id, "IDENTITY", "BEGIN", {
            "anilist_id": known.get("anilist"),
            "mal_id": known.get("mal"),
            "kitsu_id": known.get("kitsu"),
            "simkl_id": known.get("simkl"),
            "media_format": item.get("media_format"),
        })

        redirect_ids = self._simkl_ids(known)
        search_ids, search_evidence = self._search_candidate_ids(known)
        candidate_ids = []
        for value in list(redirect_ids) + list(search_ids):
            if value and value not in candidate_ids:
                candidate_ids.append(value)
        _trace(local_id, "SIMKL_DISCOVERY", "CANDIDATES", {
            "redirect_candidates": list(redirect_ids),
            "search_id": search_evidence,
            "candidate_ids": candidate_ids,
        })

        if not candidate_ids:
            _trace(local_id, "SIMKL_DISCOVERY", "NOT_FOUND",
                   reason="redirect and exact search/id returned no Simkl candidates")
            return {}

        exact = []
        rejected = []
        parent_candidates = []
        for simkl_id in candidate_ids:
            detail = self._detail(simkl_id)
            resolved = self._resolved_ids(detail, simkl_id)
            disagreements = self._disagreements(known, resolved)
            if disagreements:
                parent_candidates.append(simkl_id)
                rejected.append({
                    "simkl_id": simkl_id,
                    "resolved_ids": resolved,
                    "disagreements": disagreements,
                })
                continue
            exact.append((simkl_id, resolved))

        if exact:
            unique = {simkl_id for simkl_id, _ in exact}
            if len(unique) > 1:
                raise IdentityMappingConflict(
                    "Simkl exact tracker mappings resolve to multiple anime IDs: {}".format(
                        ", ".join(sorted(unique))
                    )
                )
            result = dict(exact[0][1])
            _trace(local_id, "SIMKL_IDENTITY", "RESOLVED", result)
            return result

        if self._special_capable(item):
            for reference in parent_candidates:
                special = self._special_reference(item, reference)
                if special:
                    _trace(local_id, "SIMKL_SPECIAL", "REFERENCE_RESOLVED", {
                        "simkl_reference_id": special.get("_simkl_reference_id"),
                        "special_locator": special.get("_special_locator"),
                        "evidence": special.get("_special_identity_evidence"),
                    })
                    return special

        if rejected:
            details = []
            for row in rejected:
                for provider, values in sorted(row["disagreements"].items()):
                    details.append(
                        "Simkl {} {} {} != {}".format(
                            row["simkl_id"], provider, values[0], values[1]
                        )
                    )
            reason = "; ".join(details)
            _trace(local_id, "SIMKL_IDENTITY", "CONFLICT", rejected, reason=reason,
                   level="warning")
            raise IdentityMappingConflict(reason)

        _trace(local_id, "SIMKL_IDENTITY", "NOT_FOUND",
               reason="no candidate survived exact tracker-ID validation")
        return {}


class SimklFirstWatchlistIdentityEnrichmentService(WatchlistIdentityEnrichmentService):
    """Own identity acquisition and only release Simkl-usable rows to mediation."""

    def __init__(self, store, client=None, request_delay=0.25, on_complete=None,
                 on_progress=None, network_timeout=30, halt_requested=None):
        self._external_halt_requested = halt_requested or (lambda: False)
        if client is None:
            client = ProviderIdentityClient(
                simkl=SimklFirstIdentityClient(
                    timeout=network_timeout,
                    halt_requested=lambda: (
                        self._stop.is_set()
                        or self._stopping.is_set()
                        or self._external_halt_requested()
                    ),
                ),
                kitsu=KitsuIdentityClient(
                    timeout=network_timeout,
                    halt_requested=lambda: (
                        self._stop.is_set()
                        or self._stopping.is_set()
                        or self._external_halt_requested()
                    ),
                ),
            )
        super().__init__(
            store,
            client=client,
            request_delay=request_delay,
            on_complete=on_complete,
            on_progress=on_progress,
            network_timeout=network_timeout,
            halt_requested=halt_requested,
        )

    @staticmethod
    def _simkl_usable(item):
        item = item or {}
        if item.get("simkl_id") not in (None, ""):
            return True
        return (
            item.get("simkl_reference_id") not in (None, "")
            and item.get("special_locator") not in (None, "")
        )

    def _hold_from_mediator(self, local_id, reason):
        clearer = getattr(self.store, "clear_mediator_ready", None)
        if clearer:
            try:
                clearer(local_id)
            except KeyError:
                return False
        _trace(local_id, "MEDIATOR_GATE", "HOLD", reason=reason)
        return False

    def _release_if_present(self, local_id):
        current = getattr(self.store, "item", lambda _id: None)(str(local_id))
        if current is None:
            return False
        if not self._simkl_usable(current):
            return self._hold_from_mediator(
                local_id,
                "Simkl-only mediator requires simkl_id or exact Simkl special reference",
            )
        released = super()._release_if_present(local_id)
        if released:
            _trace(local_id, "MEDIATOR_GATE", "RELEASE", {
                "simkl_id": current.get("simkl_id"),
                "simkl_reference_id": current.get("simkl_reference_id"),
                "special_locator": current.get("special_locator"),
                "identity_status": current.get("identity_resolution_status"),
            })
        return released

    def _record_if_present(self, local_id, status, error=None):
        recorded = super()._record_if_present(local_id, status, error=error)
        if recorded:
            current = getattr(self.store, "item", lambda _id: None)(str(local_id)) or {}
            _trace(local_id, "IDENTITY", "STATE", {
                "status": status,
                "error": error,
                "anilist_id": current.get("anilist_id"),
                "mal_id": current.get("mal_id"),
                "kitsu_id": current.get("kitsu_id"),
                "simkl_id": current.get("simkl_id"),
                "simkl_reference_id": current.get("simkl_reference_id"),
                "special_locator": current.get("special_locator"),
            })
        return recorded
