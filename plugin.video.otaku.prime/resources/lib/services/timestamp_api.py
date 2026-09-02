# -*- coding: utf-8 -*-
"""Attach small runtime APIs to Prime's existing HTTP server."""
from __future__ import annotations

import threading
from urllib.parse import urlsplit

from resources.lib.logging_config import get_logger
from resources.lib.services.age_content_policy import AgeContentPolicyStore
from resources.lib.services.age_watchlist_store import AgePolicyWatchlistWatchdogStore


LOGGER = get_logger(__name__)
PREFIX = "/api/library/episodes/"
SUFFIX = "/segments"
AGE_POLICY_PATH = "/api/preferences/age-policy"
MATURE_PATH = "/api/preferences/mature"
WATCHLIST_ITEMS_PATH = "/api/watchlist/items"


def attach_timestamp_api(server, catalog_store, on_age_policy_changed=None):
    """Attach timestamp, age-policy, and age-visible watchlist endpoints."""
    handler = getattr(server, "RequestHandlerClass", None)
    if handler is None or getattr(handler, "_prime_timestamp_api_attached", False):
        return server

    age_policy = AgeContentPolicyStore(catalog_store.db_path)
    age_policy.initialize()
    age_visible_watchlist = AgePolicyWatchlistWatchdogStore(catalog_store.db_path)
    age_visible_watchlist.initialize()
    original_get = handler.do_GET
    original_post = handler.do_POST

    def reconcile_async():
        if not on_age_policy_changed:
            return
        def run():
            try:
                on_age_policy_changed()
            except Exception:
                LOGGER.exception("Prime Physical age-policy reconciliation failed")
        threading.Thread(
            target=run,
            name="OtakuPrimeAgePolicyReconcile",
            daemon=True,
        ).start()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == AGE_POLICY_PATH:
            if not self._current_user():
                self._send_json(401, {"ok": False, "message": "Sign in again."})
                return
            self._send_json(200, {"ok": True, "policy": age_policy.state()})
            return
        if path == WATCHLIST_ITEMS_PATH:
            if not self._current_user():
                self._send_json(401, {"ok": False, "message": "Sign in again."})
                return
            self._send_json(
                200, {"ok": True, "entries": age_visible_watchlist.list_ui_items()}
            )
            return
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

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in (AGE_POLICY_PATH, MATURE_PATH):
            return original_post(self)
        if not self._current_user():
            self._send_json(401, {"ok": False, "message": "Sign in again."})
            return
        payload = self._read_api_payload()
        try:
            if path == AGE_POLICY_PATH and "birth_date" in payload:
                policy = age_policy.set_birth_date(payload.get("birth_date"))
                LOGGER.info(
                    "Administrator birth date locked: age=%s scope=%s path=%s",
                    policy.get("age"), policy.get("storage_scope"), policy.get("storage_path"),
                )
            elif "mature" in payload:
                policy = age_policy.set_mature(payload.get("mature"))
                LOGGER.info(
                    "Administrator mature-content filter changed: mature=%s age=%s",
                    policy.get("mature"), policy.get("age"),
                )
            else:
                raise ValueError("birth_date or mature is required")
        except ValueError as exc:
            self._send_json(400, {"ok": False, "message": str(exc)})
            return

        # Respond immediately. Physical-library reconciliation can involve Kodi
        # JSON-RPC and disk work and must never hold the HTTP request open.
        self._send_json(200, {"ok": True, "policy": policy, "preferences": policy})
        reconcile_async()

    handler.do_GET = do_GET
    handler.do_POST = do_POST
    handler._prime_timestamp_api_attached = True
    LOGGER.info(
        "Prime runtime APIs attached: episode segments + administrator age policy"
    )
    return server
