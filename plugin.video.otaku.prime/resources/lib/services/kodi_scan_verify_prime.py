# -*- coding: utf-8 -*-
"""Verify Kodi library imports by Prime's stable local IDs."""
from __future__ import annotations

import json
import os


PLAYBACK_PREFIX = "plugin://plugin.video.otaku.prime/play/library/"


def _normalized_directory(value):
    path = str(value or "").strip().replace("\\", "/")
    if path and not path.endswith("/"):
        path += "/"
    return path


def _normalized_file(value):
    return str(value or "").strip().replace("\\", "/").rstrip("/")


def _prime_strm_expectations(directory):
    """Map each Prime local ID to its physical STRM and plugin playback target."""
    root = str(directory or "")
    if not root or not os.path.isdir(root):
        return {}
    result = {}
    for current, _directories, filenames in os.walk(root):
        for filename in filenames:
            if not filename.lower().endswith(".strm"):
                continue
            path = os.path.join(current, filename)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    target = handle.readline(512).strip()
            except (OSError, UnicodeError):
                continue
            if not target.startswith(PLAYBACK_PREFIX):
                continue
            local_id = target[len(PLAYBACK_PREFIX):].split("?", 1)[0].strip("/").lower()
            if not local_id:
                continue
            result[local_id] = {
                "path": _normalized_file(path),
                "target": _normalized_file(target),
            }
    return result


def _prime_ids_on_disk(directory):
    return set(_prime_strm_expectations(directory))


def _json_rpc(method, params=None):
    import xbmc

    request = {"jsonrpc": "2.0", "method": str(method), "id": 1}
    if params is not None:
        request["params"] = params
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if response.get("error"):
        raise RuntimeError("Kodi {} failed: {}".format(method, response["error"]))
    return response.get("result") or {}


def _prime_unique_ids(rows):
    result = set()
    for row in rows or []:
        unique = row.get("uniqueid") or {}
        if not isinstance(unique, dict):
            continue
        value = str(unique.get("prime") or "").strip().lower()
        if value:
            result.add(value)
    return result


def _prime_ids_by_file(rows, expectations):
    """Recover Prime IDs from Kodi's file field when an older row lacks uniqueid.

    Kodi can expose a STRM either as its physical .strm path or as the plugin URL
    stored inside the STRM. Both locators are exact and contain no title matching,
    so they are safe verification fallbacks while a later RefreshTVShow/Movie
    reloads the adjacent NFO and persists Prime's uniqueid.
    """
    locators = {}
    for local_id, expected in (expectations or {}).items():
        for value in (expected.get("path"), expected.get("target")):
            normalized = _normalized_file(value)
            if normalized:
                locators[normalized] = local_id

    result = set()
    for row in rows or []:
        value = _normalized_file(row.get("file"))
        local_id = locators.get(value)
        if local_id:
            result.add(local_id)
    return result


def verify_prime_series(directory):
    """Confirm every released Prime episode in this folder exists in Kodi."""
    wanted = _normalized_directory(directory)
    expectations = _prime_strm_expectations(directory)
    expected = set(expectations)
    if not expected:
        return {
            "complete": True,
            "reason": "no_released_strm",
            "expected": 0,
            "known": 0,
            "known_uniqueid": 0,
            "known_file": 0,
            "missing": [],
            "path": wanted,
        }

    shows = _json_rpc(
        "VideoLibrary.GetTVShows", {"properties": ["file"]}
    ).get("tvshows", [])
    show = next(
        (
            row for row in shows
            if _normalized_directory(row.get("file")) == wanted
        ),
        None,
    )
    if show:
        episodes = _json_rpc(
            "VideoLibrary.GetEpisodes",
            {
                "tvshowid": int(show["tvshowid"]),
                "properties": ["uniqueid", "file"],
            },
        ).get("episodes", [])
    else:
        # Folder matching can differ between Kodi platforms/special paths. Fall
        # back to Prime's globally unique episode IDs/file targets before declaring
        # the scan missing; this is slower only on the failure/ambiguity path.
        episodes = _json_rpc(
            "VideoLibrary.GetEpisodes", {"properties": ["uniqueid", "file"]}
        ).get("episodes", [])

    by_uniqueid = expected & _prime_unique_ids(episodes)
    by_file = (expected - by_uniqueid) & _prime_ids_by_file(episodes, expectations)
    known = by_uniqueid | by_file
    missing = sorted(expected - known)
    return {
        "complete": not missing,
        "reason": (
            "complete" if not missing and not by_file
            else "complete_with_strm_fallback" if not missing
            else "prime_episode_ids_missing"
        ),
        "tvshowid": int(show["tvshowid"]) if show else None,
        "expected": len(expected),
        "known": len(known),
        "known_uniqueid": len(by_uniqueid),
        "known_file": len(by_file),
        "fallback_ids": sorted(by_file),
        "missing": missing,
        "path": wanted,
    }


def verify_prime_movie(directory):
    """Confirm the Prime movie in this folder exists in Kodi."""
    wanted = _normalized_directory(directory)
    expectations = _prime_strm_expectations(directory)
    expected = set(expectations)
    if not expected:
        return {
            "complete": True,
            "reason": "no_released_strm",
            "expected": 0,
            "known": 0,
            "known_uniqueid": 0,
            "known_file": 0,
            "missing": [],
            "path": wanted,
        }

    movies = _json_rpc(
        "VideoLibrary.GetMovies", {"properties": ["uniqueid", "file"]}
    ).get("movies", [])
    by_uniqueid = expected & _prime_unique_ids(movies)
    by_file = (expected - by_uniqueid) & _prime_ids_by_file(movies, expectations)
    known = by_uniqueid | by_file
    missing = sorted(expected - known)
    matching = next(
        (
            row for row in movies
            if str((row.get("uniqueid") or {}).get("prime") or "").strip().lower()
            in expected
            or _normalized_file(row.get("file")) in {
                value
                for item in expectations.values()
                for value in (item.get("path"), item.get("target"))
                if value
            }
        ),
        None,
    )
    return {
        "complete": not missing,
        "reason": (
            "complete" if not missing and not by_file
            else "complete_with_strm_fallback" if not missing
            else "prime_movie_id_missing"
        ),
        "movieid": int(matching["movieid"]) if matching else None,
        "expected": len(expected),
        "known": len(known),
        "known_uniqueid": len(by_uniqueid),
        "known_file": len(by_file),
        "fallback_ids": sorted(by_file),
        "missing": missing,
        "path": wanted,
    }
