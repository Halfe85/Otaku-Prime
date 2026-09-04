# -*- coding: utf-8 -*-
"""Structured, watchlist-scoped tracing for Prime mediation.

Every mediation log line starts with the same watchlist-local ID so one item can
be followed from the watchdog handoff through provider resolution, catalogue
commit, physical projection, or a terminal failure.
"""
from __future__ import annotations

import json
import threading

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
_SENSITIVE_MARKERS = ("token", "password", "secret", "cookie", "authorization", "credential")
_SEQUENCE_LOCK = threading.Lock()
_SEQUENCES = {}


def _redact(value, key=None):
    """Make evidence JSON safe and deterministic without leaking credentials."""
    if key and any(marker in str(key).casefold() for marker in _SENSITIVE_MARKERS):
        return "<redacted>"
    if isinstance(value, dict):
        return {str(k): _redact(v, k) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_redact(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _json(value):
    return json.dumps(
        _redact(value), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )


class MediatorTrace:
    """Emit one stable trace stream for a single Prime watchlist item."""

    def __init__(self, watchlist_local_id, reset=False):
        self.watchlist_local_id = str(watchlist_local_id or "UNKNOWN")
        if reset:
            with _SEQUENCE_LOCK:
                _SEQUENCES[self.watchlist_local_id] = 0

    def _next_sequence(self):
        with _SEQUENCE_LOCK:
            value = int(_SEQUENCES.get(self.watchlist_local_id, 0)) + 1
            _SEQUENCES[self.watchlist_local_id] = value
            return value

    def _emit(self, level, stage, event, facts=None, reason=None):
        sequence = self._next_sequence()
        parts = [
            "MEDIATOR[{}]".format(self.watchlist_local_id),
            "seq={:03d}".format(sequence),
            "stage={}".format(str(stage or "unknown")),
            "event={}".format(str(event or "unknown")),
        ]
        if reason not in (None, ""):
            parts.append("reason={}".format(_json(str(reason))))
        if facts is not None:
            parts.append("facts={}".format(_json(facts)))
        getattr(LOGGER, level)(" ".join(parts))

    def info(self, stage, event, facts=None, reason=None):
        self._emit("info", stage, event, facts=facts, reason=reason)

    def warning(self, stage, event, facts=None, reason=None):
        self._emit("warning", stage, event, facts=facts, reason=reason)

    def error(self, stage, event, facts=None, reason=None):
        self._emit("error", stage, event, facts=facts, reason=reason)


def watchlist_input_facts(item):
    """Return mediation-relevant watchlist facts; never raw account/auth data."""
    item = item or {}
    keys = (
        "local_id", "simkl_id", "simkl_reference_id", "special_locator",
        "anilist_id", "mal_id", "kitsu_id",
        "identity_resolution_status", "media_format", "episode_count",
        "episodes", "num_episodes", "english_name", "romaji_name", "title",
        "release_date", "release_status", "progress", "is_adult",
    )
    return {key: item.get(key) for key in keys if key in item}


def placement_facts(placement):
    """Flatten a placement into the evidence most useful while diagnosing it."""
    placement = placement or {}
    owner = placement.get("structural_owner") or {}
    show = placement.get("tv_show") or {}
    components = placement.get("seasons") or []
    mappings = []
    if components:
        iterable = []
        for component in components:
            season = (component.get("season") or {}).get("number")
            iterable.extend((season, row) for row in component.get("episodes") or [])
    else:
        season = (placement.get("season") or {}).get("number")
        iterable = [(season, row) for row in placement.get("episodes") or []]
    for season_number, row in iterable:
        mappings.append({
            "source_episode": row.get("source_episode_number"),
            "tvdb_season": row.get("season_number", season_number),
            "tvdb_episode": row.get("episode_number"),
            "simkl_episode_id": row.get("simkl_id"),
            "title": row.get("title"),
            "release_date": row.get("release_date"),
        })
    return {
        "provider_path": placement.get("provider_path"),
        "provider_id": placement.get("provider_id"),
        "provider_reference_id": placement.get("provider_reference_id"),
        "library_type": placement.get("library_type"),
        "simkl_relation_path_observed": placement.get("relation_path"),
        "mediation_evidence": placement.get("mediation_evidence"),
        "provider_attempts": placement.get("provider_attempts"),
        "structural_owner": owner,
        "catalogue_owner": {
            "name": show.get("name"),
            "romaji_name": show.get("romaji_name"),
            "tvdb_id": show.get("tvdb_id"),
            "simkl_id": show.get("simkl_id"),
            "source": show.get("source"),
        },
        "season": placement.get("season"),
        "season_count": len(components) if components else 1,
        "episode_mapping_count": len(mappings),
        "episode_mappings": mappings,
    }
