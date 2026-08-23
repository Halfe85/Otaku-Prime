# -*- coding: utf-8 -*-
"""Authentication and session handling for Otaku Prime."""

from __future__ import annotations

import secrets
import threading
import time
from typing import Optional


SESSION_TTL_SECONDS = 12 * 60 * 60


class AuthService:
    """Authenticate local users and own their in-memory web sessions."""

    def __init__(self, user_store) -> None:
        self._user_store = user_store
        self._sessions = {}
        self._lock = threading.Lock()

    def login(self, username: str, password: str) -> Optional[str]:
        user = self._user_store.authenticate(username, password)
        if user is None:
            return None

        token = secrets.token_urlsafe(32)
        with self._lock:
            self._sessions[token] = {
                "user": user,
                "expires": time.time() + SESSION_TTL_SECONDS,
            }
        return token

    def current_user(self, token: str) -> Optional[dict]:
        if not token:
            return None

        with self._lock:
            session = self._sessions.get(token)
            if session is None:
                return None
            if session["expires"] <= time.time():
                self._sessions.pop(token, None)
                return None
            return session["user"]

    def logout(self, token: str) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def change_password(
        self,
        token: str,
        current_password: str,
        new_password: str,
    ) -> Optional[str]:
        user = self.current_user(token)
        if user is None:
            return "not_authenticated"

        verified = self._user_store.authenticate(
            user["username"],
            current_password,
        )
        if verified is None:
            return "incorrect_password"
        if len(new_password) < 8:
            return "password_too_short"

        self._user_store.update_password(user["id"], new_password)
        self.logout(token)
        return None
