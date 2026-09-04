# -*- coding: utf-8 -*-
"""Kodi cleanup helpers keyed by Prime unique IDs rather than mutable paths."""
from __future__ import annotations

import json

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)


def _json_rpc(method, params=None):
    try:
        import xbmc
    except (ImportError, RuntimeError) as exc:
        raise RuntimeError("Kodi JSON-RPC is unavailable") from exc
    request = {"jsonrpc": "2.0", "method": str(method), "id": 1}
    if params is not None:
        request["params"] = params
    response = json.loads(xbmc.executeJSONRPC(json.dumps(request)))
    if response.get("error"):
        raise RuntimeError("Kodi {} failed: {}".format(method, response["error"]))
    return response.get("result") or {}


def _normalized_directory(value):
    path = str(value or "").strip().replace("\\", "/")
    if path and not path.endswith("/"):
        path += "/"
    return path


def _parent_directory(value):
    path = str(value or "").strip().replace("\\", "/").rstrip("/")
    return _normalized_directory(path.rsplit("/", 1)[0]) if "/" in path else ""


def _prime_unique_id(row):
    value = (row or {}).get("uniqueid") or {}
    if not isinstance(value, dict):
        return ""
    return str(value.get("prime") or "").strip().lower()


def remove_prime_tvshows(prime_id=None, directories=None):
    """Remove every Kodi TV-show row matching a Prime ID or stale generated path."""
    wanted_id = str(prime_id or "").strip().lower()
    wanted_paths = {
        _normalized_directory(value) for value in (directories or []) if value
    }
    result = _json_rpc(
        "VideoLibrary.GetTVShows", {"properties": ["file", "uniqueid"]}
    )
    matches = []
    for row in result.get("tvshows", []) or []:
        row_id = _prime_unique_id(row)
        row_path = _normalized_directory(row.get("file"))
        if (wanted_id and row_id == wanted_id) or (wanted_paths and row_path in wanted_paths):
            matches.append(row)
        elif not wanted_id and not wanted_paths and row_id:
            matches.append(row)

    removed = []
    for row in matches:
        kodi_id = int(row["tvshowid"])
        _json_rpc("VideoLibrary.RemoveTVShow", {"tvshowid": kodi_id})
        removed.append({
            "tvshowid": kodi_id,
            "prime_id": _prime_unique_id(row),
            "path": _normalized_directory(row.get("file")),
        })
    if removed:
        LOGGER.warning("Removed %s Prime TV-show rows from Kodi", len(removed))
    return {"removed": len(removed), "items": removed}


def remove_prime_movies(prime_id=None, directories=None):
    """Remove every Kodi movie row matching a Prime ID or stale generated path."""
    wanted_id = str(prime_id or "").strip().lower()
    wanted_paths = {
        _normalized_directory(value) for value in (directories or []) if value
    }
    result = _json_rpc(
        "VideoLibrary.GetMovies", {"properties": ["file", "uniqueid"]}
    )
    matches = []
    for row in result.get("movies", []) or []:
        row_id = _prime_unique_id(row)
        row_path = _parent_directory(row.get("file"))
        if (wanted_id and row_id == wanted_id) or (wanted_paths and row_path in wanted_paths):
            matches.append(row)
        elif not wanted_id and not wanted_paths and row_id:
            matches.append(row)

    removed = []
    for row in matches:
        kodi_id = int(row["movieid"])
        _json_rpc("VideoLibrary.RemoveMovie", {"movieid": kodi_id})
        removed.append({
            "movieid": kodi_id,
            "prime_id": _prime_unique_id(row),
            "path": _parent_directory(row.get("file")),
        })
    if removed:
        LOGGER.warning("Removed %s Prime movie rows from Kodi", len(removed))
    return {"removed": len(removed), "items": removed}


def remove_all_prime_video():
    """Remove all Kodi rows that advertise a Prime unique ID."""
    tv = remove_prime_tvshows()
    movies = remove_prime_movies()
    return {
        "tvshows": tv["removed"],
        "movies": movies["removed"],
        "removed": tv["removed"] + movies["removed"],
    }
