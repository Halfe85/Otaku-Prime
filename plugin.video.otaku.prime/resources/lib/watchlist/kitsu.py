# -*- coding: utf-8 -*-
"""Direct Kitsu authentication helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode


KITSU_API_URL = "https://kitsu.io/api"


class KitsuAuthError(RuntimeError):
    """Raised when Kitsu rejects credentials or returns invalid data."""


@dataclass(frozen=True)
class KitsuConnection:
    user_id: int
    username: str
    access_token: str
    refresh_token: str
    expires_at: int


class KitsuAuthenticator:
    def __init__(self, timeout: int = 15) -> None:
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return True

    @staticmethod
    def _json(response_body: bytes) -> dict:
        try:
            data = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KitsuAuthError("Kitsu returned an invalid response.") from exc
        if not isinstance(data, dict):
            raise KitsuAuthError("Kitsu returned an unexpected response.")
        return data

    def connect(self, username: str, password: str) -> KitsuConnection:
        login_name = username.strip()
        if not login_name or not password:
            raise KitsuAuthError("Enter your Kitsu username or email and password.")

        token_request = Request(
            KITSU_API_URL + "/oauth/token",
            data=json.dumps({
                "grant_type": "password",
                "username": login_name,
                "password": password,
            }).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1",
            },
        )
        try:
            with urlopen(token_request, timeout=self.timeout) as response:
                token_data = self._json(response.read())
        except HTTPError as exc:
            if exc.code in (400, 401, 403):
                raise KitsuAuthError("Kitsu rejected the username or password.") from exc
            raise KitsuAuthError("Kitsu login failed (HTTP {}).".format(exc.code)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise KitsuAuthError("Unable to reach Kitsu.") from exc

        access_token = str(token_data.get("access_token", "")).strip()
        refresh_token = str(token_data.get("refresh_token", "")).strip()
        try:
            expires_in = int(token_data.get("expires_in", 0))
        except (TypeError, ValueError):
            expires_in = 0
        if not access_token or not refresh_token or expires_in <= 0:
            raise KitsuAuthError("Kitsu did not return complete credentials.")

        profile_request = Request(
            KITSU_API_URL + "/edge/users?filter[self]=true",
            headers={
                "Authorization": "Bearer " + access_token,
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
                "User-Agent": "Otaku-Prime/0.1",
            },
        )
        try:
            with urlopen(profile_request, timeout=self.timeout) as response:
                profile = self._json(response.read())
        except HTTPError as exc:
            raise KitsuAuthError("Kitsu rejected the new access token.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise KitsuAuthError("Unable to verify the Kitsu account.") from exc

        users = profile.get("data")
        if not isinstance(users, list) or not users or not isinstance(users[0], dict):
            raise KitsuAuthError("Kitsu did not return an authenticated user.")
        user = users[0]
        try:
            user_id = int(user["id"])
            verified_name = str(user["attributes"]["name"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise KitsuAuthError("Kitsu returned an invalid user profile.") from exc
        if not verified_name:
            raise KitsuAuthError("Kitsu returned an empty username.")

        return KitsuConnection(
            user_id=user_id,
            username=verified_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=int(time.time()) + expires_in,
        )

    def refresh(self, refresh_token: str) -> tuple[str, str, int]:
        request=Request(KITSU_API_URL+"/oauth/token",data=urlencode({
          "grant_type":"refresh_token","refresh_token":refresh_token}).encode("utf-8"),
          method="POST",headers={"Content-Type":"application/x-www-form-urlencoded",
          "Accept":"application/json","User-Agent":"Otaku-Prime/0.1.2"})
        try:
            with urlopen(request,timeout=self.timeout) as response: data=self._json(response.read())
        except HTTPError as exc:
            raise KitsuAuthError("Kitsu rejected the refresh token.") from exc
        except (URLError,TimeoutError,OSError) as exc:
            raise KitsuAuthError("Unable to refresh Kitsu authorization.") from exc
        access=str(data.get("access_token") or "").strip()
        refreshed=str(data.get("refresh_token") or refresh_token).strip()
        expires=int(data.get("expires_in") or 0)
        if not access or not refreshed or expires<=0:
            raise KitsuAuthError("Kitsu returned incomplete refreshed credentials.")
        return access,refreshed,int(time.time())+expires
