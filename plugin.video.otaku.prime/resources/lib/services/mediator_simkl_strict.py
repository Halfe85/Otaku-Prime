# -*- coding: utf-8 -*-
"""Strict Simkl -> TVDB placement endpoint for the mediator rebuild.

The target Simkl media item is the only identity context used to resolve TVDB
structure. PREQUEL/SEQUEL relation traversal is deliberately not consulted for
catalogue ownership or season numbering in this phase.
"""
from __future__ import annotations

from resources.lib.services.mediator_endpoint_simkl import SimklMediatorEndpoint, _LOCATOR
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
    SPECIAL_MEDIA_TYPES,
    _episodes,
    _remote_title,
)


class _MappedRowsClient:
    """Keep every Simkl row that carries an explicit TVDB coordinate."""

    def __init__(self, client):
        self._client = client

    def __getattr__(self, name):
        return getattr(self._client, name)

    def episodes(self, simkl_id):
        rows = self._client.episodes(simkl_id)
        result = []
        for row in rows or []:
            value = dict(row)
            tvdb = value.get("tvdb") or {}
            if tvdb.get("season") is not None and tvdb.get("episode") is not None:
                value["type"] = "episode"
            result.append(value)
        return result


class StrictStructuralSimklMediatorEndpoint(SimklMediatorEndpoint):
    """Resolve one Simkl target into explicit TVDB-backed Prime structure."""

    @staticmethod
    def _validate_exact_target(simkl_id, target):
        returned = str((target.get("ids") or {}).get("simkl") or "")
        if returned != str(simkl_id):
            raise MediatorPlacementError(
                "Simkl returned identity {} while {} was requested".format(returned, simkl_id)
            )

    @staticmethod
    def _target_evidence(target, structural_owner, coordinate_source):
        ids = dict((target or {}).get("ids") or {})
        return {
            "simkl_target": {
                "title": (target or {}).get("title"),
                "english_title": (target or {}).get("en_title"),
                "anime_type": (target or {}).get("anime_type"),
                "year": (target or {}).get("year"),
                "status": (target or {}).get("status") or (target or {}).get("release_status"),
                "ids": ids,
                "mapped_tvdb_seasons": list((target or {}).get("mapped_tvdb_seasons") or []),
            },
            "structural_owner": dict(structural_owner or {}),
            "coordinate_source": coordinate_source,
            "relation_traversal_used_for_ownership": False,
            "relation_traversal_used_for_season_number": False,
        }

    @staticmethod
    def _validate_rows(candidates):
        missing = []
        for row in candidates:
            if row.get("season_number") is None or row.get("episode_number") is None:
                missing.append(row.get("source_episode_number"))
        if missing:
            raise MediatorPlacementError(
                "Simkl source episodes lack explicit TVDB coordinates: {}".format(
                    sorted(set(missing), key=lambda value: str(value))
                )
            )

    @staticmethod
    def _component(target, target_type, season_number, rows, source):
        episodes = sorted(rows, key=lambda row: int(row.get("episode_number") or 0))
        if not episodes:
            raise MediatorMetadataPending(
                "Simkl returned no episodes for TVDB season {}".format(season_number)
            )
        numbers = [int(row["episode_number"]) for row in episodes]
        if (
            int(season_number) != 0
            and len(numbers) > 1
            and numbers != list(range(numbers[0], numbers[-1] + 1))
        ):
            raise MediatorPlacementError(
                "Simkl TVDB season {} coordinates contain gaps: {}".format(
                    season_number, numbers
                )
            )
        return {
            "season": {
                "number": int(season_number),
                "number_source": source,
                "name": _remote_title(target),
                "media_type": target_type,
                "first_episode": numbers[0],
                "last_episode": numbers[-1],
                "structural_season_number": int(season_number),
            },
            "episodes": episodes,
        }

    @staticmethod
    def _special_exact_matches(item, raw_rows, season_number, first_episode, last_episode):
        """Return exact external-ID evidence for the requested special range."""
        matches = []
        for row in raw_rows or []:
            tvdb = row.get("tvdb") or {}
            try:
                season = int(tvdb.get("season"))
                episode = int(tvdb.get("episode"))
            except (TypeError, ValueError):
                continue
            if season != season_number or not first_episode <= episode <= last_episode:
                continue
            ids = row.get("ids") or {}
            for provider in ("anilist", "mal", "kitsu"):
                local = item.get(provider + "_id")
                remote = ids.get(provider)
                if local not in (None, "") and remote not in (None, "") and str(local) == str(remote):
                    matches.append({
                        "provider": provider,
                        "provider_id": str(local),
                        "tvdb_season": season,
                        "tvdb_episode": episode,
                    })
        return matches

    def _exact(self, item, client):
        simkl_id = str(item["simkl_id"])
        target = client.anime(simkl_id)
        self._validate_exact_target(simkl_id, target)

        # Relation roots are intentionally ignored. The target itself supplies
        # identity context for Simkl's TV/TVDB cross-map.
        franchise = self._franchise_identity(target, target)
        structural_owner = self._structural_owner(client, target, target)
        target_type = str(target.get("anime_type") or "").lower()

        if target_type == "movie" and structural_owner.get("tvdb_id") in (None, ""):
            return {
                "provider_path": "simkl",
                "provider_id": simkl_id,
                "provider_reference_id": None,
                "library_type": "movie",
                "tv_show": franchise,
                "structural_owner": None,
                "season": {
                    "number": 0,
                    "number_source": "standalone_simkl_movie",
                    "name": _remote_title(target),
                    "media_type": target_type,
                    "first_episode": None,
                    "last_episode": None,
                    "structural_season_number": None,
                },
                "episodes": [],
                "relation_path": [simkl_id],
                "mediation_evidence": self._target_evidence(
                    target, None, "standalone_simkl_movie"
                ),
            }

        if structural_owner.get("tvdb_id") in (None, ""):
            raise MediatorPlacementError(
                "Simkl target {} has no TVDB structural series owner".format(simkl_id)
            )

        candidates = _episodes(
            client.episodes(simkl_id), target_type in SPECIAL_MEDIA_TYPES
        )
        if not candidates:
            raise MediatorMetadataPending(
                "Simkl returned no episode rows for target {}".format(simkl_id)
            )
        self._validate_rows(candidates)

        season_numbers = sorted({int(row["season_number"]) for row in candidates})
        if not season_numbers:
            raise MediatorPlacementError(
                "Simkl target {} returned no explicit TVDB season coordinates".format(simkl_id)
            )

        components = [
            self._component(
                target,
                target_type,
                season_number,
                [row for row in candidates if int(row["season_number"]) == season_number],
                "explicit_tvdb_coordinates",
            )
            for season_number in season_numbers
        ]

        result = {
            "provider_path": "simkl",
            "provider_id": simkl_id,
            "provider_reference_id": None,
            "library_type": "series",
            "tv_show": franchise,
            "structural_owner": structural_owner,
            "season": components[0]["season"],
            "episodes": components[0]["episodes"],
            "relation_path": [simkl_id],
            "mediation_evidence": self._target_evidence(
                target, structural_owner, "explicit_tvdb_coordinates"
            ),
        }
        if len(components) > 1:
            result["seasons"] = components
        return result

    def _referenced_special(self, item, client):
        reference = str(item.get("simkl_reference_id") or "")
        match = _LOCATOR.match(str(item.get("special_locator") or "").upper())
        if not reference or not match:
            raise MediatorPlacementError("Simkl special reference is incomplete")

        season_number = int(match.group(1))
        first_episode = int(match.group(2))
        last_episode = int(match.group(3) or first_episode)
        if last_episode < first_episode:
            raise MediatorPlacementError("Simkl special reference range is reversed")

        target = client.anime(reference)
        self._validate_exact_target(reference, target)
        franchise = self._franchise_identity(target, target)
        structural_owner = self._structural_owner(client, target, target)
        if structural_owner.get("tvdb_id") in (None, ""):
            raise MediatorPlacementError(
                "Simkl special reference {} has no TVDB structural series owner".format(reference)
            )

        raw_rows = client.episodes(reference)
        exact_matches = self._special_exact_matches(
            item, raw_rows, season_number, first_episode, last_episode
        )
        if not exact_matches:
            raise MediatorPlacementError(
                "Simkl special reference {} {} has no exact AniList/MAL/Kitsu ID evidence; "
                "fuzzy title/date locators are not accepted by the strict mediator".format(
                    reference, item.get("special_locator")
                )
            )

        candidates = _episodes(raw_rows, True)
        self._validate_rows(candidates)
        selected = [
            row for row in candidates
            if int(row.get("season_number", -1)) == season_number
            and first_episode <= int(row.get("episode_number", -1)) <= last_episode
        ]
        selected.sort(key=lambda row: int(row["episode_number"]))
        expected = list(range(first_episode, last_episode + 1))
        if [int(row["episode_number"]) for row in selected] != expected:
            raise MediatorPlacementError(
                "Simkl reference {} has no exact TVDB coordinate range {}".format(
                    reference, item.get("special_locator")
                )
            )

        evidence = self._target_evidence(
            target, structural_owner, "watchlist_special_locator_exact_id_verified"
        )
        evidence["special_exact_id_matches"] = exact_matches
        return {
            "provider_path": "simkl",
            "provider_id": None,
            "provider_reference_id": reference,
            "library_type": "series",
            "tv_show": franchise,
            "structural_owner": structural_owner,
            "season": {
                "number": season_number,
                "number_source": "watchlist_special_locator_exact_id_verified",
                "name": item.get("english_name") or item.get("romaji_name"),
                "media_type": str(item.get("media_format") or "SPECIAL").lower(),
                "first_episode": first_episode,
                "last_episode": last_episode,
                "structural_season_number": season_number,
            },
            "episodes": selected,
            "relation_path": [reference],
            "special_locator": item.get("special_locator"),
            "mediation_evidence": evidence,
        }

    def resolve(self, item, client=None):
        base = client or self.client
        if base is None:
            from resources.lib.services.mediator_helper_simkl import SimklMediatorClient
            base = SimklMediatorClient()
        mapped = _MappedRowsClient(base)
        if item.get("simkl_id") not in (None, ""):
            return self._exact(item, mapped)
        if (
            item.get("simkl_reference_id") not in (None, "")
            and item.get("special_locator") not in (None, "")
        ):
            return self._referenced_special(item, mapped)
        raise MediatorPlacementError(
            "watchlist item has no Simkl identity or Simkl special reference"
        )
