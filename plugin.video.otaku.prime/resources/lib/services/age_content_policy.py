# -*- coding: utf-8 -*-
"""Prime age policy shared by the admin UI and Kodi physical library."""
from __future__ import annotations

import datetime
import json
import sqlite3
import threading
from contextlib import contextmanager

from resources.lib.logging_config import get_logger
from resources.lib.services.system_age_profile import (
    BirthDateLockedError,
    SystemAgeProfile,
    SystemAgeProfileError,
)


LOGGER = get_logger(__name__)
RATING_G = "G"
RATING_PG = "PG"
RATING_PG13 = "PG-13"
RATING_R = "R"
RATING_R_PLUS = "R+"
RATING_RX = "RX"


def _terms(value):
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item or "").strip()]
    return []


def parse_birth_date(value):
    """Parse the admin-facing DD/MM/YYYY value into an ISO date string."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.strptime(text, "%d/%m/%Y").date()
    except ValueError as exc:
        raise ValueError("birth date must use DD/MM/YYYY") from exc
    today = datetime.date.today()
    if parsed > today:
        raise ValueError("birth date cannot be in the future")
    try:
        oldest = today.replace(year=today.year - 120)
    except ValueError:
        oldest = today.replace(month=2, day=28, year=today.year - 120)
    if parsed < oldest:
        raise ValueError("birth date is outside the supported age range")
    return parsed.isoformat()


def display_birth_date(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return datetime.date.fromisoformat(text).strftime("%d/%m/%Y")
    except ValueError:
        return ""


def age_years(value, today=None):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        born = datetime.date.fromisoformat(text)
    except ValueError:
        return None
    today = today or datetime.date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def normalize_rating(row):
    """Normalize provider rating strings to Prime's age-policy buckets."""
    row = row or {}
    raw = str(row.get("age_rating") or "").strip().upper()
    compact = raw.replace(" ", "")
    genres = _terms(row.get("genres")) or _terms(row.get("genres_json"))
    explicit_adult = bool(row.get("mature")) or any(
        str(value).strip().casefold() == "hentai" for value in genres
    )

    if compact.startswith("RX"):
        return RATING_RX
    if compact.startswith("R+"):
        return RATING_R_PLUS
    if compact == "R" or compact.startswith("R-") or compact.startswith("R("):
        return RATING_R
    if compact.startswith("PG-13") or compact.startswith("PG13"):
        return RATING_PG13
    if compact == "PG" or compact.startswith("PG-") or compact.startswith("PG("):
        return RATING_PG
    if compact == "G" or compact.startswith("G-") or compact.startswith("G("):
        return RATING_G
    if explicit_adult:
        return RATING_RX
    return raw or None


def evaluate_content(row, age=None, mature_enabled=False):
    """Return UI-artwork and Kodi-admission decisions for one title."""
    rating = normalize_rating(row)
    known_age = None if age is None else max(0, int(age))
    adult = known_age is not None and known_age >= 18
    mature_enabled = bool(mature_enabled) and adult

    if rating == RATING_RX:
        allowed = adult and mature_enabled
        return {"rating": rating, "kodi_allowed": allowed, "blur_ui": not allowed,
                "minimum_age": 18,
                "reason": "allowed" if allowed else "rx_requires_adult_mature_filter"}
    if rating in (RATING_R, RATING_R_PLUS):
        allowed = known_age is not None and known_age >= 15
        return {"rating": rating, "kodi_allowed": allowed, "blur_ui": not allowed,
                "minimum_age": 15,
                "reason": "allowed" if allowed else "rating_requires_age_15"}
    if rating == RATING_PG13:
        allowed = known_age is not None and known_age >= 10
        return {"rating": rating, "kodi_allowed": allowed, "blur_ui": False,
                "minimum_age": 10,
                "reason": "allowed" if allowed else "rating_requires_age_10"}
    if rating in (RATING_G, RATING_PG):
        return {"rating": rating, "kodi_allowed": True, "blur_ui": False,
                "minimum_age": 0, "reason": "always_allowed"}
    return {"rating": rating, "kodi_allowed": True, "blur_ui": False,
            "minimum_age": None, "reason": "unrated_allowed"}


class AgeContentPolicyStore:
    """Persist Mature in Prime and DOB in one locked OS-user profile."""

    def __init__(self, db_path, system_profile=None, system_profile_path=None):
        self.db_path = str(db_path)
        self._system_profile = system_profile or SystemAgeProfile(system_profile_path)
        self._initialized = False
        self._initialize_lock = threading.RLock()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _legacy_birth_date(self):
        with self._connection() as db:
            row = db.execute(
                "SELECT birth_date FROM prime_age_preferences WHERE singleton=1"
            ).fetchone()
        return str(row["birth_date"] or "").strip() if row else ""

    def initialize(self):
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            with self._connection() as db:
                # Kept only to migrate Alpha installs that stored DOB in addon SQLite.
                db.execute("""CREATE TABLE IF NOT EXISTS prime_age_preferences(
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  birth_date TEXT,
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                db.execute("""INSERT OR IGNORE INTO prime_age_preferences(singleton,birth_date)
                  VALUES(1,NULL)""")
                db.execute("""CREATE TABLE IF NOT EXISTS watchlist_preferences(
                  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                  mature INTEGER NOT NULL DEFAULT 0 CHECK(mature IN(0,1)),
                  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )""")
                db.execute("""INSERT OR IGNORE INTO watchlist_preferences(singleton,mature)
                  VALUES(1,0)""")
                legacy_row = db.execute(
                    "SELECT birth_date FROM prime_age_preferences WHERE singleton=1"
                ).fetchone()
                legacy_birth_date = str(
                    legacy_row["birth_date"] or ""
                ).strip() if legacy_row else ""

            profile = self._system_profile.read()
            if not profile["exists"] and legacy_birth_date:
                try:
                    self._system_profile.write_once(legacy_birth_date)
                    profile = self._system_profile.read()
                    LOGGER.info(
                        "Migrated Prime birth date from Kodi addon data to locked OS-user profile: %s",
                        profile.get("path"),
                    )
                except (BirthDateLockedError, SystemAgeProfileError, OSError) as exc:
                    # Keep the old value as a locked fallback for this install.
                    LOGGER.error(
                        "Could not migrate Prime birth date to OS-user profile %s: %s",
                        self._system_profile.path,
                        exc,
                    )

            profile = self._system_profile.read()
            if profile.get("birth_date"):
                with self._connection() as db:
                    # System profile is authoritative; remove duplicate DOB from
                    # addon-owned SQLite after migration.
                    db.execute("""UPDATE prime_age_preferences SET birth_date=NULL,
                      updated_at=CURRENT_TIMESTAMP WHERE singleton=1 AND birth_date IS NOT NULL""")
                effective_birth_date = profile["birth_date"]
            elif profile["exists"]:
                # Corrupt existing profile fails closed and stays locked.
                effective_birth_date = None
            else:
                effective_birth_date = legacy_birth_date or None

            age = age_years(effective_birth_date)
            if age is None or age < 18:
                with self._connection() as db:
                    db.execute("""UPDATE watchlist_preferences SET mature=0,
                      updated_at=CURRENT_TIMESTAMP WHERE singleton=1 AND mature<>0""")
            self._initialized = True

    def state(self):
        self.initialize()
        profile = self._system_profile.read()
        legacy_birth_date = "" if profile["exists"] else self._legacy_birth_date()
        if profile.get("birth_date"):
            birth_date = profile["birth_date"]
        elif profile["exists"]:
            birth_date = None
        else:
            birth_date = legacy_birth_date or None

        with self._connection() as db:
            mature_row = db.execute(
                "SELECT mature FROM watchlist_preferences WHERE singleton=1"
            ).fetchone()
        age = age_years(birth_date)
        mature_allowed = age is not None and age >= 18
        mature = int(mature_row["mature"] if mature_row else 0)
        if not mature_allowed:
            mature = 0
        return {
            "birth_date": birth_date,
            "birth_date_display": display_birth_date(birth_date),
            "birth_date_locked": bool(profile["exists"] or legacy_birth_date),
            "age": age,
            "mature": mature,
            "mature_allowed": bool(mature_allowed),
            "storage_scope": "os_user",
            "storage_path": profile.get("path") or self._system_profile.path,
            "storage_persistent": bool(profile.get("birth_date")),
            "storage_error": profile.get("error"),
        }

    def set_birth_date(self, value):
        iso_date = parse_birth_date(value)
        if not iso_date:
            raise ValueError("birth date is required and can only be set once")
        self.initialize()
        current = self.state()
        if current.get("birth_date_locked"):
            if current.get("birth_date") == iso_date and not current.get("storage_error"):
                return current
            raise BirthDateLockedError(
                "Birth date is already set and cannot be changed in Otaku Prime."
            )
        try:
            self._system_profile.write_once(iso_date)
        except BirthDateLockedError:
            raise
        except (SystemAgeProfileError, OSError) as exc:
            raise ValueError(
                "could not store the operating-system age profile: {}".format(exc)
            ) from exc

        with self._connection() as db:
            db.execute("""UPDATE prime_age_preferences SET birth_date=NULL,
              updated_at=CURRENT_TIMESTAMP WHERE singleton=1""")
            if age_years(iso_date) is None or age_years(iso_date) < 18:
                db.execute("""UPDATE watchlist_preferences SET mature=0,
                  updated_at=CURRENT_TIMESTAMP WHERE singleton=1""")
        LOGGER.info("Prime birth date locked to OS-user profile: %s", self._system_profile.path)
        return self.state()

    def set_mature(self, value):
        state = self.state()
        if isinstance(value, str):
            text = value.strip().lower()
            if text not in ("0", "1", "false", "true", "off", "on", "no", "yes"):
                raise ValueError("mature must be 0 or 1")
            mature = 1 if text in ("1", "true", "on", "yes") else 0
        elif value in (0, 1, False, True):
            mature = int(bool(value))
        else:
            raise ValueError("mature must be 0 or 1")
        if mature and not state["mature_allowed"]:
            raise ValueError("Mature content can only be enabled for age 18 or older")
        with self._connection() as db:
            db.execute("""UPDATE watchlist_preferences SET mature=?,
              updated_at=CURRENT_TIMESTAMP WHERE singleton=1""", (mature,))
        return self.state()

    def evaluate(self, row):
        state = self.state()
        result = evaluate_content(row, age=state.get("age"),
                                  mature_enabled=state.get("mature"))
        result.update({"age": state.get("age"),
                       "birth_date": state.get("birth_date"),
                       "mature": state.get("mature"),
                       "mature_allowed": state.get("mature_allowed")})
        return result
