# -*- coding: utf-8 -*-
"""Remove Prime-generated physical entries that no longer satisfy age policy."""
from __future__ import annotations

import json
import os
import shutil

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)


def _normalized_directory(value):
    path = str(value or "").strip().replace("\\", "/")
    if path and not path.endswith("/"):
        path += "/"
    return path


def _parent_directory(value):
    path = str(value or "").strip().replace("\\", "/").rstrip("/")
    if not path or "/" not in path:
        return ""
    return _normalized_directory(path.rsplit("/", 1)[0])


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


def _prime_unique_id(row):
    unique = (row or {}).get("uniqueid") or {}
    return str(unique.get("prime") or "").strip().lower() if isinstance(unique, dict) else ""


def remove_tvshow_from_kodi(series_id, directory):
    """Remove a TV show by stable Prime ID, with folder matching as a fallback."""
    result = _json_rpc(
        "VideoLibrary.GetTVShows", {"properties": ["file", "uniqueid"]}
    )
    wanted_id = str(series_id or "").strip().lower()
    wanted_path = _normalized_directory(directory)
    show = next(
        (
            row for row in result.get("tvshows", [])
            if _prime_unique_id(row) == wanted_id
            or _normalized_directory(row.get("file")) == wanted_path
        ),
        None,
    )
    if not show:
        return {"removed": False, "reason": "not_in_kodi", "path": wanted_path}
    kodi_id = int(show["tvshowid"])
    removed = _json_rpc("VideoLibrary.RemoveTVShow", {"tvshowid": kodi_id})
    LOGGER.info(
        "Kodi age policy removed TV show: prime=%s tvshowid=%s path=%s",
        wanted_id, kodi_id, wanted_path,
    )
    return {"removed": True, "tvshowid": kodi_id, "result": removed, "path": wanted_path}


def remove_movie_from_kodi(movie_id, directory):
    """Remove a movie by stable Prime ID, with folder matching as a fallback."""
    result = _json_rpc(
        "VideoLibrary.GetMovies", {"properties": ["file", "uniqueid"]}
    )
    wanted_id = str(movie_id or "").strip().lower()
    wanted_path = _normalized_directory(directory)
    movie = next(
        (
            row for row in result.get("movies", [])
            if _prime_unique_id(row) == wanted_id
            or _parent_directory(row.get("file")) == wanted_path
        ),
        None,
    )
    if not movie:
        return {"removed": False, "reason": "not_in_kodi", "path": wanted_path}
    kodi_id = int(movie["movieid"])
    removed = _json_rpc("VideoLibrary.RemoveMovie", {"movieid": kodi_id})
    LOGGER.info(
        "Kodi age policy removed movie: prime=%s movieid=%s path=%s",
        wanted_id, kodi_id, wanted_path,
    )
    return {"removed": True, "movieid": kodi_id, "result": removed, "path": wanted_path}


def remove_prime_directory(directory, allowed_root):
    """Delete only a generated title directory below the expected Prime root."""
    directory = os.path.abspath(str(directory or ""))
    root = os.path.abspath(str(allowed_root or ""))
    if not directory or not root or directory == root:
        return {"removed": False, "reason": "unsafe_path"}
    try:
        inside = os.path.commonpath((root, directory)) == root
    except ValueError:
        inside = False
    if not inside:
        raise RuntimeError("refusing to remove a directory outside Prime library root")
    if not os.path.isdir(directory):
        return {"removed": False, "reason": "directory_missing", "path": directory}
    shutil.rmtree(directory)
    LOGGER.info("Prime age policy removed physical library directory: %s", directory)
    return {"removed": True, "path": directory}
