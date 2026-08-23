# -*- coding: utf-8 -*-
"""Provider-neutral authentication API used by the Otaku Prime web service."""

from __future__ import annotations

from typing import Optional

from resources.lib.watchlist.anilist import AniListAuthError, AniListAuthenticator


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

    def list_providers(self) -> dict:
        return {
            "providers": [
                {
                    "id": "anilist",
                    "name": "AniList",
                    "configured": self._anilist.configured,
                    "flow": "oauth2_implicit_pin",
                    "redirect_url": "https://anilist.co/api/v2/oauth/pin",
                }
            ]
        }

    def provider_info(self, provider: str) -> dict:
        name = provider.strip().lower()
        if name != "anilist":
            raise AuthenticatorAPIError(
                "unsupported_provider",
                f"Unsupported authentication provider: {provider}",
                404,
            )

        info = self.list_providers()["providers"][0].copy()
        if self._anilist.configured:
            try:
                info["authorize_url"] = self._anilist.authorization_url()
            except AniListAuthError:
                info["configured"] = False
        return info

    def authorization_url(self, provider: str) -> str:
        name = provider.strip().lower()
        if name != "anilist":
            raise AuthenticatorAPIError(
                "unsupported_provider",
                f"Unsupported authentication provider: {provider}",
                404,
            )

        try:
            return self._anilist.authorization_url()
        except AniListAuthError as exc:
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
