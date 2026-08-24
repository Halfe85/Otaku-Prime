# -*- coding: utf-8 -*-
"""Local user storage for the Otaku Prime web interface."""

from __future__ import annotations

import hashlib
import hmac
import os
import sqlite3
from contextlib import contextmanager
from typing import Optional


PBKDF2_ITERATIONS = 200_000


class UserStore:
    """Own the SQLite database used only for Otaku Prime web users."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _hash_password(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )

    def initialize(self) -> None:
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_iterations INTEGER NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user'
                        CHECK (role IN ('admin', 'user')),
                    is_active INTEGER NOT NULL DEFAULT 1
                        CHECK (is_active IN (0, 1)),
                    must_change_password INTEGER NOT NULL DEFAULT 0
                        CHECK (must_change_password IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS app_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )

            bootstrap = db.execute(
                "SELECT value FROM app_state WHERE key = 'users_bootstrapped'"
            ).fetchone()

            if bootstrap is None:
                self._create_user_in_connection(
                    db,
                    username="admin",
                    password="admin",
                    role="admin",
                    must_change_password=True,
                )
                db.execute(
                    "INSERT INTO app_state(key, value) VALUES('users_bootstrapped', '1')"
                )

    def _create_user_in_connection(
        self,
        db: sqlite3.Connection,
        *,
        username: str,
        password: str,
        role: str,
        must_change_password: bool,
    ) -> int:
        username = username.strip()
        if not username:
            raise ValueError("username cannot be empty")
        if not password:
            raise ValueError("password cannot be empty")

        salt = os.urandom(16)
        password_hash = self._hash_password(password, salt, PBKDF2_ITERATIONS)
        cursor = db.execute(
            """
            INSERT INTO users(
                username,
                password_hash,
                password_salt,
                password_iterations,
                role,
                must_change_password
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                username,
                password_hash.hex(),
                salt.hex(),
                PBKDF2_ITERATIONS,
                role,
                1 if must_change_password else 0,
            ),
        )
        return int(cursor.lastrowid)

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        with self._connection() as db:
            row = db.execute(
                """
                SELECT id, username, password_hash, password_salt,
                       password_iterations, role, is_active,
                       must_change_password
                FROM users
                WHERE username = ? COLLATE NOCASE
                """,
                (username.strip(),),
            ).fetchone()

        if row is None or not row["is_active"]:
            return None

        salt = bytes.fromhex(row["password_salt"])
        actual = self._hash_password(
            password,
            salt,
            int(row["password_iterations"]),
        ).hex()

        if not hmac.compare_digest(actual, row["password_hash"]):
            return None

        return {
            "id": int(row["id"]),
            "username": row["username"],
            "role": row["role"],
            "must_change_password": bool(row["must_change_password"]),
        }

    def update_password(self, user_id: int, new_password: str) -> None:
        if not new_password:
            raise ValueError("password cannot be empty")

        salt = os.urandom(16)
        password_hash = self._hash_password(
            new_password,
            salt,
            PBKDF2_ITERATIONS,
        )

        with self._connection() as db:
            db.execute(
                """
                UPDATE users
                SET password_hash = ?,
                    password_salt = ?,
                    password_iterations = ?,
                    must_change_password = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    password_hash.hex(),
                    salt.hex(),
                    PBKDF2_ITERATIONS,
                    user_id,
                ),
            )
