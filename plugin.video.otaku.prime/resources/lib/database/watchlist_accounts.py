# -*- coding: utf-8 -*-
"""Persistent watchlist-provider account storage for Otaku Prime."""

from __future__ import annotations

import sqlite3
from typing import Optional


class WatchlistAccountStore:
    """Store one authenticated account per local user/provider."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_accounts (
                    user_id INTEGER NOT NULL,
                    provider TEXT NOT NULL,
                    external_user_id TEXT NOT NULL,
                    external_username TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    connected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, provider),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
                )
                """
            )

    def get(self, user_id: int, provider: str) -> Optional[dict]:
        with self._connect() as db:
            row = db.execute(
                """
                SELECT user_id, provider, external_user_id, external_username,
                       connected_at, updated_at
                FROM watchlist_accounts
                WHERE user_id = ? AND provider = ?
                """,
                (int(user_id), provider),
            ).fetchone()
        return dict(row) if row else None

    def save(
        self,
        *,
        user_id: int,
        provider: str,
        external_user_id: str,
        external_username: str,
        access_token: str,
    ) -> None:
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO watchlist_accounts(
                    user_id, provider, external_user_id, external_username,
                    access_token, connected_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    external_user_id = excluded.external_user_id,
                    external_username = excluded.external_username,
                    access_token = excluded.access_token,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    int(user_id), provider, str(external_user_id),
                    external_username, access_token,
                ),
            )

    def delete(self, user_id: int, provider: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM watchlist_accounts WHERE user_id = ? AND provider = ?",
                (int(user_id), provider),
            )
