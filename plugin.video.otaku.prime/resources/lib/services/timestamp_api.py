# -*- coding: utf-8 -*-
"""Attach the episode timestamp endpoint to Prime's existing HTTP server."""
from __future__ import annotations

from urllib.parse import urlsplit

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
PREFIX = "/api/library/episodes/"
SUFFIX = "/segments"


def attach_timestamp_api(server, catalog_store):
    """Add GET /api/library/episodes/<Prime ID>/segments to a Prime server."""
    handler = getattr(server, "RequestHandlerClass", None)
    if handler is None or getattr(handler, "_prime_timestamp_api_attached", False):
        return server

    original_get = handler.do_GET

    def do_GET(self):
        path = urlsplit(self.path).path
        if path.startswith(PREFIX) and path.endswith(SUFFIX):
            if not self._current_user():
                self._send_json(401, {"ok": False, "message": "Sign in again."})
                return
            episode_id = path[len(PREFIX):-len(SUFFIX)].strip("/").lower()
            if len(episode_id) != 18 or any(
                char not in "0123456789abcdef" for char in episode_id
            ):
                self._send_json(
                    400, {"ok": False, "message": "Invalid Prime episode ID."}
                )
                return
            getter = getattr(catalog_store, "episode_timestamp_metadata", None)
            metadata = getter(episode_id) if getter else None
            if not metadata:
                self._send_json(404, {"ok": False, "message": "Episode not found."})
                return
            self._send_json(200, {"ok": True, **metadata})
            return
        return original_get(self)

    handler.do_GET = do_GET
    handler._prime_timestamp_api_attached = True
    LOGGER.info(
        "Prime timestamp API attached: GET /api/library/episodes/<episode_id>/segments"
    )
    return server
