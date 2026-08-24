# -*- coding: utf-8 -*-
"""Provider-neutral authentication API used by the Otaku Prime web service."""

from __future__ import annotations

from typing import Optional

from resources.lib.watchlist.anilist import AniListAuthError, AniListAuthenticator
from resources.lib.watchlist.mal import MALAuthError, MALAuthenticator


class AuthenticatorAPIError(RuntimeError):
    """A safe error that can be returned by the local authentication API."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class AuthenticatorAPI:
    """Resolve provider auth URLs and validate provider access tokens."""

    def __init__(self, anilist_client_id: Optional[str] = None) -> None:
        self._anilist = AniListAuthenticator(client_id=anilist_client_id)
        self._mal = MALAuthenticator()

    def list_providers(self) -> dict:
        return {
            "providers": [
                {
                    "id": "anilist",
                    "name": "AniList",
                    "configured": self._anilist.configured,
                    "flow": "armkai_oauth_pin",
                    "authorize_url": self._anilist.authorization_url(),
                },
                {
                    "id": "mal",
                    "name": "MyAnimeList",
                    "configured": self._mal.configured,
                    "flow": "armkai_oauth_pkce",
                    "authorize_url": self._mal.authorization_url(),
                },
            ]
        }

    def provider_info(self, provider: str) -> dict:
        name = provider.strip().lower()
        providers = {item["id"]: item for item in self.list_providers()["providers"]}
        if name not in providers:
            raise AuthenticatorAPIError(
                "unsupported_provider",
                f"Unsupported authentication provider: {provider}",
                404,
            )

        return providers[name].copy()

    def authorization_url(self, provider: str) -> str:
        name = provider.strip().lower()
        if name not in ("anilist", "mal"):
            raise AuthenticatorAPIError(
                "unsupported_provider",
                f"Unsupported authentication provider: {provider}",
                404,
            )

        try:
            authenticator = self._anilist if name == "anilist" else self._mal
            return authenticator.authorization_url()
        except (AniListAuthError, MALAuthError) as exc:
            raise AuthenticatorAPIError("provider_not_configured", str(exc), 503) from exc

    def verify_token(self, provider: str, token: str) -> dict:
        name = provider.strip().lower()
        if name != "anilist":
            raise AuthenticatorAPIError(
                "unsupported_provider",
                f"Unsupported authentication provider: {provider}",
                404,
            )

        try:
            viewer = self._anilist.verify_access_token(token)
        except AniListAuthError as exc:
            raise AuthenticatorAPIError("invalid_credentials", str(exc), 401) from exc

        return {
            "ok": True,
            "provider": "anilist",
            "user": {"id": viewer.user_id, "username": viewer.username},
        }

    def connect_mal(self, callback_url: str) -> dict:
        try:
            connection = self._mal.connect(callback_url)
        except MALAuthError as exc:
            raise AuthenticatorAPIError("invalid_credentials", str(exc), 401) from exc
        return {
            "ok": True,
            "provider": "mal",
            "user": {"id": connection.user_id, "username": connection.username},
            "access_token": connection.access_token,
            "refresh_token": connection.refresh_token,
            "expires_at": connection.expires_at,
        }
