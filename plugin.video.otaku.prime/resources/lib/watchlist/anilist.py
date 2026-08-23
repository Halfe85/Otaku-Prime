# -*- coding: utf-8 -*-
"""AniList OAuth and authenticated API helpers for Otaku Prime."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


AUTHORIZE_URL = "https://anilist.co/api/v2/oauth/authorize"
GRAPHQL_URL = "https://graphql.anilist.co"
PIN_REDIRECT_URL = "https://anilist.co/api/v2/oauth/pin"

# AniList client IDs are public identifiers. Put the Otaku Prime client ID here
# before release. The environment override is useful for development/testing.
PACKAGED_CLIENT_ID = ""
CLIENT_ID_ENV = "OTAKU_PRIME_ANILIST_CLIENT_ID"


class AniListAuthError(RuntimeError):
    """Raised when AniList authorization or token validation fails."""


@dataclass(frozen=True)
class AniListViewer:
    """Minimal identity returned after validating an AniList access token."""

    user_id: int
    username: str


class AniListAuthenticator:
    """Build AniList PIN-flow URLs and validate resulting access tokens."""

    def __init__(self, client_id: Optional[str] = None, timeout: int = 15) -> None:
        self.client_id = (client_id or self._default_client_id()).strip()
        self.timeout = timeout

    @staticmethod
    def _default_client_id() -> str:
        return os.environ.get(CLIENT_ID_ENV, "").strip() or PACKAGED_CLIENT_ID.strip()

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    def authorization_url(self) -> str:
        """Return the official AniList implicit-grant authorization URL."""
        if not self.configured:
            raise AniListAuthError(
                "AniList client ID is not configured. Set PACKAGED_CLIENT_ID "
                f"or the {CLIENT_ID_ENV} environment variable."
            )

        return f"{AUTHORIZE_URL}?{urlencode({'client_id': self.client_id, 'response_type': 'token'})}"

    @staticmethod
    def _decode_response(response_body: bytes) -> dict:
        try:
            data = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AniListAuthError("AniList returned an invalid response.") from exc
        if not isinstance(data, dict):
            raise AniListAuthError("AniList returned an unexpected response.")
        return data

    def verify_access_token(self, token: str) -> AniListViewer:
        """Validate an access token and return the authenticated AniList user."""
        access_token = token.strip()
        if not access_token:
            raise AniListAuthError("AniList access token is empty.")

        query = """
        query {
          Viewer {
            id
            name
          }
        }
        """
        payload = json.dumps({"query": query}).encode("utf-8")
        request = Request(
            GRAPHQL_URL,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                data = self._decode_response(response.read())
        except HTTPError as exc:
            # Do not include the access token or upstream response body in errors/logs.
            if exc.code in (401, 403):
                raise AniListAuthError("AniList rejected the access token.") from exc
            raise AniListAuthError(f"AniList authentication request failed (HTTP {exc.code}).") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AniListAuthError("Unable to reach AniList.") from exc

        errors = data.get("errors")
        if errors:
            raise AniListAuthError("AniList rejected the authentication request.")

        viewer = data.get("data", {}).get("Viewer")
        if not isinstance(viewer, dict) or viewer.get("id") is None or not viewer.get("name"):
            raise AniListAuthError("AniList did not return an authenticated user.")

        try:
            user_id = int(viewer["id"])
        except (TypeError, ValueError) as exc:
            raise AniListAuthError("AniList returned an invalid user ID.") from exc

        return AniListViewer(user_id=user_id, username=str(viewer["name"]))
