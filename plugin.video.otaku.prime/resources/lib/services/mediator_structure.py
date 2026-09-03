# -*- coding: utf-8 -*-
"""Structural ownership and coverage policy for Prime mediation.

Relationship graphs are deliberately transient. They may help a provider find
candidate ownership, but Prime persists only the resolved series/season/episode
structure. This module keeps structural identity separate from source metadata
and prevents incomplete provider responses from becoming final placements.
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
    """Fail safe for TV items when no structural cross-map is available.

    A PREQUEL/SEQUEL chain proves continuity, not same-series ownership. If the
    selected provider has no TVDB-backed structural owner, a TV/TV_SHORT item is
    kept as its own series rather than being folded into a relation root. This
    can under-merge during an outage, but cannot corrupt another Prime series.
    """
    result = deepcopy(placement or {})
    media_format = str((item or {}).get("media_format") or "").strip().upper()
    show = result.setdefault("tv_show", {})
    if media_format not in TV_FORMATS or show.get("tvdb_id") not in (None, ""):
        return result

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
    show["source_format"] = media_format
    show["source"] = "target_series_safe_fallback"
    season = result.setdefault("season", {})
    season["number"] = 1
    season["number_source"] = "target_series_safe_fallback"
    components = result.get("seasons") or []
    if components:
        rows = placement_rows(result)
        # Without a structural cross-map we cannot safely preserve a provider's
        # inferred multi-season split. Collapse only the requested TV item into
        # its own S01; source episode identity is retained on every row.
        result.pop("seasons", None)
        result["episodes"] = rows
        for row in result["episodes"]:
            row["season_number"] = 1
    return result


def apply_structural_hint(item, placement, structural_hint):
    """Use a partial structural result only as ownership/coordinate evidence.

    This is the important Bakemonogatari/Owarimonogatari case: Simkl may know the
    correct TVDB series and season but expose fewer source units than AniList or
    MAL. The complete source placement may borrow that structural owner when the
    hint points to exactly one season. A partial hint spanning multiple seasons
    is not safe to synthesize and is therefore rejected.
    """
    if not structural_hint:
        return safe_target_series_fallback(item, placement)
    hint_numbers = placement_season_numbers(structural_hint)
    if len(hint_numbers) != 1:
        return None

    result = deepcopy(placement or {})
    rows = placement_rows(result)
    hint_show = (structural_hint or {}).get("tv_show") or {}
    show = result.setdefault("tv_show", {})
    for key in ("name", "romaji_name", "simkl_id", "tvdb_id", "source_format"):
        value = hint_show.get(key)
        if value not in (None, ""):
            show[key] = value
    show["source"] = "structural_hint+{}".format(
        str(show.get("source") or result.get("provider_path") or "provider")
    )

    season_number = next(iter(hint_numbers))
    season = result.setdefault("season", {})
    season["number"] = season_number
    season["number_source"] = "partial_structural_hint"
    result.pop("seasons", None)
    result["episodes"] = rows
    for row in rows:
        row["season_number"] = season_number
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
                # Simkl may label a TVDB-mapped recap/web episode as `special`.
                # The structural coordinate is more important than that source
                # label, so make the normal mapper retain the row.
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
