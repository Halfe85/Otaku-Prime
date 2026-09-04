# -*- coding: utf-8 -*-
"""Structural ownership and coverage policy for Prime mediation.

Relationship graphs are transient. Prime persists the selected franchise identity
plus structural series/season/episode evidence, never the relation graph itself.
"""
from __future__ import annotations

from copy import deepcopy

from resources.lib.logging_config import get_logger
from resources.lib.services.mediator_endpoint_simkl import SimklMediatorEndpoint


LOGGER = get_logger(__name__)
TV_FORMATS = {"TV", "TV_SHORT"}


def _integer(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def expected_episode_count(item):
    """Return the watchlist's known source-unit count, when trustworthy."""
    for key in ("episode_count", "episodes", "num_episodes"):
        value = _integer((item or {}).get(key))
        if value:
            return value
    return None


def placement_rows(placement):
    """Flatten one placement without duplicating multi-season component rows."""
    placement = placement or {}
    components = placement.get("seasons") or []
    if components:
        rows = []
        for component in components:
            rows.extend(component.get("episodes") or [])
        return rows
    return list(placement.get("episodes") or [])


def placement_season_numbers(placement):
    numbers = set()
    placement = placement or {}
    components = placement.get("seasons") or []
    if components:
        for component in components:
            value = (component.get("season") or {}).get("number")
            if value is not None:
                numbers.add(int(value))
        return numbers
    value = (placement.get("season") or {}).get("number")
    if value is not None:
        numbers.add(int(value))
    return numbers


def coverage_state(item, placement):
    """Describe whether a provider covered every known source media unit."""
    if (placement or {}).get("library_type") == "movie":
        return {
            "complete": True,
            "expected": expected_episode_count(item),
            "covered": 1,
            "reason": "movie_object",
        }
    rows = placement_rows(placement)
    expected = expected_episode_count(item)
    covered = len(rows)
    if expected is None:
        return {
            "complete": bool(rows),
            "expected": None,
            "covered": covered,
            "reason": "provider_rows" if rows else "no_episode_rows",
        }
    return {
        "complete": covered == expected,
        "expected": expected,
        "covered": covered,
        "reason": "complete" if covered == expected else "incomplete_episode_coverage",
    }


def _item_title(item, *keys):
    for key in keys:
        value = str((item or {}).get(key) or "").strip()
        if value:
            return value
    return None


def safe_target_series_fallback(item, placement):
    """Keep an unresolved TV item isolated instead of fuzzy-merging it.

    A relation path is not enough evidence to let an un-mapped target mutate an
    existing Prime franchise. This intentionally under-merges rather than risking
    a destructive parent rename or ID reassignment.
    """
    result = deepcopy(placement or {})
    media_format = str((item or {}).get("media_format") or "").strip().upper()
    if media_format not in TV_FORMATS:
        return result
    if (result.get("structural_owner") or {}).get("tvdb_id") not in (None, ""):
        return result

    show = result.setdefault("tv_show", {})
    english = _item_title(item, "english_name", "title_english", "title")
    romaji = _item_title(item, "romaji_name", "title_romaji", "title")
    if english:
        show["name"] = english
    if romaji:
        show["romaji_name"] = romaji
    show["simkl_id"] = (
        str(item.get("simkl_id")) if item.get("simkl_id") not in (None, "") else None
    )
    show["anilist_id"] = (
        str(item.get("anilist_id")) if item.get("anilist_id") not in (None, "") else None
    )
    show["mal_id"] = (
        str(item.get("mal_id")) if item.get("mal_id") not in (None, "") else None
    )
    show["kitsu_id"] = (
        str(item.get("kitsu_id")) if item.get("kitsu_id") not in (None, "") else None
    )
    show["tvdb_id"] = None
    show["source_format"] = media_format
    show["source"] = "target_series_safe_fallback"
    result["structural_owner"] = None

    season = result.setdefault("season", {})
    season["number"] = 1
    season["number_source"] = "target_series_safe_fallback"
    season["structural_season_number"] = None
    components = result.get("seasons") or []
    if components:
        rows = placement_rows(result)
        result.pop("seasons", None)
        result["episodes"] = rows
        for row in rows:
            row["season_number"] = 1
    return result


def apply_structural_hint(item, placement, structural_hint):
    """Borrow TVDB coordinates without borrowing franchise title/root IDs.

    A partial Simkl result may know the target's structural TVDB owner while a
    complete AniList/MAL result supplies all source units. Only structure is
    copied from the hint; the source placement keeps its own franchise identity.
    """
    if not structural_hint:
        return safe_target_series_fallback(item, placement)
    hint_numbers = placement_season_numbers(structural_hint)
    if len(hint_numbers) != 1:
        return None

    result = deepcopy(placement or {})
    rows = placement_rows(result)
    owner = deepcopy((structural_hint or {}).get("structural_owner") or {})
    if not owner.get("tvdb_id"):
        return safe_target_series_fallback(item, placement)
    result["structural_owner"] = owner

    season_number = next(iter(hint_numbers))
    season = result.setdefault("season", {})
    season["number"] = season_number
    season["number_source"] = "partial_structural_hint"
    season["structural_season_number"] = season_number
    result.pop("seasons", None)
    result["episodes"] = rows
    for row in rows:
        row["season_number"] = season_number
    result.setdefault("structural_provenance", {})["source"] = "partial_simkl_hint"
    return result


class _MappedRowsClient:
    """Proxy that keeps every Simkl row carrying explicit TVDB coordinates."""

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
                # The explicit TVDB coordinate is structural evidence even when
                # Simkl classifies the source row as a special/recap.
                value["type"] = "episode"
            result.append(value)
        return result


class StructuralSimklMediatorEndpoint(SimklMediatorEndpoint):
    """Simkl endpoint with structural-coordinate preservation enabled."""

    def resolve(self, item, client=None):
        base = client or self.client
        if base is None:
            return super().resolve(item, client=client)
        return super().resolve(item, client=_MappedRowsClient(base))
