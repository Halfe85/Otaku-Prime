# -*- coding: utf-8 -*-
"""MyAnimeList OAuth PKCE helpers for the ArmKai authorization flow."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen


ARMKAI_AUTH_URL = "https://armkai.vercel.app/api/mal"
MAL_CLIENT_ID = "a8d85a4106b259b8c9470011ce2f76bc"
MAL_TOKEN_URL = "https://myanimelist.net/v1/oauth2/token"
MAL_API_URL = "https://api.myanimelist.net/v2"


class MALAuthError(RuntimeError):
    """Raised when MAL authorization or identity validation fails."""


@dataclass(frozen=True)
class MALConnection:
    user_id: int
    username: str
    access_token: str
    refresh_token: str
    expires_at: int


class MALAuthenticator:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return True

    def authorization_url(self) -> str:
        return ARMKAI_AUTH_URL

    @staticmethod
    def _json(response_body: bytes, service: str) -> dict:
        try:
            data = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MALAuthError("{} returned an invalid response.".format(service)) from exc
        if not isinstance(data, dict):
            raise MALAuthError("{} returned an unexpected response.".format(service))
        return data

    @staticmethod
    def _callback_values(callback_url: str):
        parsed = urlparse(callback_url.strip())
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in ("myanimelist.net", "www.myanimelist.net"):
            raise MALAuthError("Paste the complete HTTPS redirect URL from MyAnimeList.")
        params = parse_qs(parsed.query)
        code = params.get("code", [""])[0].strip()
        verifier = params.get("state", [""])[0].strip()
        if not code or not verifier:
            raise MALAuthError("The MyAnimeList redirect URL is missing its authorization code or state.")
        return code, verifier

    def connect(self, callback_url: str) -> MALConnection:
        code, verifier = self._callback_values(callback_url)
        token_request = Request(
            MAL_TOKEN_URL,
            data=urlencode({
                "client_id": MAL_CLIENT_ID,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
            }).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1",
            },
        )
        try:
            with urlopen(token_request, timeout=self.timeout) as response:
                token_data = self._json(response.read(), "MyAnimeList")
        except HTTPError as exc:
            raise MALAuthError("MyAnimeList rejected the authorization URL.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MALAuthError("Unable to reach MyAnimeList.") from exc

        access_token = str(token_data.get("access_token", "")).strip()
        refresh_token = str(token_data.get("refresh_token", "")).strip()
        try:
            expires_in = int(token_data.get("expires_in", 0))
        except (TypeError, ValueError):
            expires_in = 0
        if not access_token or not refresh_token or expires_in <= 0:
            raise MALAuthError("MyAnimeList did not return complete credentials.")

        profile_request = Request(
            MAL_API_URL + "/users/@me?fields=name",
            headers={
                "Authorization": "Bearer " + access_token,
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1",
            },
        )
        try:
            with urlopen(profile_request, timeout=self.timeout) as response:
                profile = self._json(response.read(), "MyAnimeList")
        except HTTPError as exc:
            raise MALAuthError("MyAnimeList rejected the new access token.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MALAuthError("Unable to verify the MyAnimeList account.") from exc

        try:
            user_id = int(profile["id"])
            username = str(profile["name"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise MALAuthError("MyAnimeList did not return an authenticated user.") from exc
        if not username:
            raise MALAuthError("MyAnimeList returned an empty username.")

        return MALConnection(
            user_id=user_id,
            username=username,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(time.time()) + expires_in,
        )
