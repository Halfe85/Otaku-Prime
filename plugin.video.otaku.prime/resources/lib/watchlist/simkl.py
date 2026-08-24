# -*- coding: utf-8 -*-
"""Simkl PIN authentication helpers."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SIMKL_API_URL = "https://api.simkl.com"
SIMKL_VERIFICATION_URL = "https://simkl.com/pin"
PACKAGED_CLIENT_ID = "59dfdc579d244e1edf6f89874d521d37a69a95a1abd349910cb056a1872ba2c8"
CLIENT_ID_ENV = "OTAKU_PRIME_SIMKL_CLIENT_ID"


class SimklAuthError(RuntimeError):
    """Raised when Simkl authentication fails."""


@dataclass(frozen=True)
class SimklDeviceCode:
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class SimklConnection:
    user_id: str
    username: str
    access_token: str


class SimklAuthenticator:
    def __init__(self, client_id: Optional[str] = None, timeout: int = 15) -> None:
        self.client_id = (client_id or os.environ.get(CLIENT_ID_ENV, "") or PACKAGED_CLIENT_ID).strip()
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.client_id)

    @staticmethod
    def _json(response_body: bytes) -> dict:
        try:
            data = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SimklAuthError("Simkl returned an invalid response.") from exc
        if not isinstance(data, dict):
            raise SimklAuthError("Simkl returned an unexpected response.")
        return data

    def _get(self, path: str, params: dict) -> dict:
        request = Request(
            SIMKL_API_URL + path + "?" + urlencode(params),
            headers={"Accept": "application/json", "User-Agent": "Otaku-Prime/0.1"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return self._json(response.read())
        except HTTPError as exc:
            raise SimklAuthError("Simkl authentication failed (HTTP {}).".format(exc.code)) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SimklAuthError("Unable to reach Simkl.") from exc

    def start(self) -> SimklDeviceCode:
        if not self.configured:
            raise SimklAuthError("Simkl client ID is not configured.")
        data = self._get("/oauth/pin", {"client_id": self.client_id})
        try:
            user_code = str(data["user_code"]).strip()
            expires_in = int(data["expires_in"])
            interval = int(data["interval"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SimklAuthError("Simkl did not return a valid device code.") from exc
        verification_url = str(data.get("verification_url") or SIMKL_VERIFICATION_URL).strip()
        if not user_code or expires_in <= 0 or interval <= 0:
            raise SimklAuthError("Simkl returned an invalid device code.")
        return SimklDeviceCode(user_code, verification_url, expires_in, interval)

    def poll(self, user_code: str) -> Optional[SimklConnection]:
        data = self._get("/oauth/pin/" + user_code, {"client_id": self.client_id})
        if data.get("result") != "OK":
            return None
        access_token = str(data.get("access_token", "")).strip()
        if not access_token:
            raise SimklAuthError("Simkl approved the code without returning a token.")

        profile_request = Request(
            SIMKL_API_URL + "/users/settings",
            data=b"{}",
            method="POST",
            headers={
                "Authorization": "Bearer " + access_token,
                "simkl-api-key": self.client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1",
            },
        )
        try:
            with urlopen(profile_request, timeout=self.timeout) as response:
                profile = self._json(response.read())
        except HTTPError as exc:
            raise SimklAuthError("Simkl rejected the new access token.") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise SimklAuthError("Unable to verify the Simkl account.") from exc

        user = profile.get("user")
        if not isinstance(user, dict):
            raise SimklAuthError("Simkl did not return an authenticated user.")
        username = str(user.get("name", "")).strip()
        identifiers = user.get("ids") if isinstance(user.get("ids"), dict) else {}
        user_id = str(user.get("id") or identifiers.get("simkl") or username).strip()
        if not username or not user_id:
            raise SimklAuthError("Simkl returned an invalid user profile.")
        return SimklConnection(user_id=user_id, username=username, access_token=access_token)
