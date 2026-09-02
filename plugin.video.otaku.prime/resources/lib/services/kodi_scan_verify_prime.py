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


def _prime_ids_on_disk(directory):
    """Read Prime local IDs from the STRM files that Kodi is expected to import."""
    root = str(directory or "")
    if not root or not os.path.isdir(root):
        return set()
    ids = set()
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
            if target.startswith(PLAYBACK_PREFIX):
                local_id = target[len(PLAYBACK_PREFIX):].split("?", 1)[0].strip("/")
                if local_id:
                    ids.add(local_id.lower())
    return ids


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


def verify_prime_series(directory):
    """Confirm every released Prime episode ID in this folder exists in Kodi."""
    wanted = _normalized_directory(directory)
    expected = _prime_ids_on_disk(directory)
    if not expected:
        return {
            "complete": True,
            "reason": "no_released_strm",
            "expected": 0,
            "known": 0,
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
                "properties": ["uniqueid"],
            },
        ).get("episodes", [])
    else:
        # Folder matching can differ between Kodi platforms/special paths. Fall
        # back to Prime's globally unique episode IDs before declaring the scan
        # missing; this is slower only on the failure/ambiguity path.
        episodes = _json_rpc(
            "VideoLibrary.GetEpisodes", {"properties": ["uniqueid"]}
        ).get("episodes", [])

    known = _prime_unique_ids(episodes)
    missing = sorted(expected - known)
    return {
        "complete": not missing,
        "reason": "complete" if not missing else "prime_episode_ids_missing",
        "tvshowid": int(show["tvshowid"]) if show else None,
        "expected": len(expected),
        "known": len(expected & known),
        "missing": missing,
        "path": wanted,
    }


def verify_prime_movie(directory):
    """Confirm the Prime movie ID in this folder exists in Kodi."""
    wanted = _normalized_directory(directory)
    expected = _prime_ids_on_disk(directory)
    if not expected:
        return {
            "complete": True,
            "reason": "no_released_strm",
            "expected": 0,
            "known": 0,
            "missing": [],
            "path": wanted,
        }

    movies = _json_rpc(
        "VideoLibrary.GetMovies", {"properties": ["uniqueid"]}
    ).get("movies", [])
    known = _prime_unique_ids(movies)
    missing = sorted(expected - known)
    matching = next(
        (
            row for row in movies
            if str((row.get("uniqueid") or {}).get("prime") or "").strip().lower()
            in expected
        ),
        None,
    )
    return {
        "complete": not missing,
        "reason": "complete" if not missing else "prime_movie_id_missing",
        "movieid": int(matching["movieid"]) if matching else None,
        "expected": len(expected),
        "known": len(expected & known),
        "missing": missing,
        "path": wanted,
    }
