# -*- coding: utf-8 -*-
"""Prime age policy shared by the admin UI and Kodi physical library."""
from __future__ import annotations

import datetime
import json
import sqlite3
from contextlib import contextmanager


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

    if compact.startswith("RX") or explicit_adult:
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
    return raw or None


def evaluate_content(row, age=None, mature_enabled=False):
    """Return UI-artwork and Kodi-admission decisions for one title."""
    rating = normalize_rating(row)
    known_age = None if age is None else max(0, int(age))
    adult = known_age is not None and known_age >= 18
    mature_enabled = bool(mature_enabled) and adult

    if rating == RATING_RX:
        allowed = adult and mature_enabled
        return {
            "rating": rating,
            "kodi_allowed": allowed,
            "blur_ui": not allowed,
            "minimum_age": 18,
            "reason": "allowed" if allowed else "rx_requires_adult_mature_filter",
        }
    if rating in (RATING_R, RATING_R_PLUS):
        allowed = known_age is not None and known_age >= 15
        return {
            "rating": rating,
            "kodi_allowed": allowed,
            "blur_ui": not allowed,
            "minimum_age": 15,
            "reason": "allowed" if allowed else "rating_requires_age_15",
        }
    if rating == RATING_PG13:
        allowed = known_age is not None and known_age >= 10
        return {
            "rating": rating,
            "kodi_allowed": allowed,
            "blur_ui": False,
            "minimum_age": 10,
            "reason": "allowed" if allowed else "rating_requires_age_10",
        }
    if rating in (RATING_G, RATING_PG):
        return {
            "rating": rating,
            "kodi_allowed": True,
            "blur_ui": False,
            "minimum_age": 0,
            "reason": "always_allowed",
        }
    # Unknown/unrated ordinary content is not blocked. Explicit adult rows were
    # normalized to RX above, so missing metadata cannot bypass the adult gate.
    return {
        "rating": rating,
        "kodi_allowed": True,
        "blur_ui": False,
        "minimum_age": None,
        "reason": "unrated_allowed",
    }


class AgeContentPolicyStore:
    """Persist the administrator DOB and enforce the adult-toggle age gate."""

    def __init__(self, db_path):
        self.db_path = str(db_path)

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS prime_age_preferences(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              birth_date TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            db.execute("""INSERT OR IGNORE INTO prime_age_preferences(singleton,birth_date)
              VALUES(1,NULL)""")
            # Older databases already have this table. Keep the mature switch in
            # its existing authoritative location but ensure it exists for clean DBs.
            db.execute("""CREATE TABLE IF NOT EXISTS watchlist_preferences(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              mature INTEGER NOT NULL DEFAULT 0 CHECK(mature IN(0,1)),
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            db.execute("""INSERT OR IGNORE INTO watchlist_preferences(singleton,mature)
              VALUES(1,0)""")
            row = db.execute(
                "SELECT birth_date FROM prime_age_preferences WHERE singleton=1"
            ).fetchone()
            age = age_years(row["birth_date"] if row else None)
            if age is None or age < 18:
                # Alpha migration safety: never carry an old enabled mature flag
                # into the new DOB-gated policy when adult age is not established.
                db.execute("""UPDATE watchlist_preferences SET mature=0,
                  updated_at=CURRENT_TIMESTAMP WHERE singleton=1 AND mature<>0""")

    def state(self):
        self.initialize()
        with self._connection() as db:
            age_row = db.execute(
                "SELECT birth_date FROM prime_age_preferences WHERE singleton=1"
            ).fetchone()
            mature_row = db.execute(
                "SELECT mature FROM watchlist_preferences WHERE singleton=1"
            ).fetchone()
        birth_date = age_row["birth_date"] if age_row else None
        age = age_years(birth_date)
        mature_allowed = age is not None and age >= 18
        mature = int(mature_row["mature"] if mature_row else 0)
        if not mature_allowed:
            mature = 0
        return {
            "birth_date": birth_date,
            "birth_date_display": display_birth_date(birth_date),
            "age": age,
            "mature": mature,
            "mature_allowed": bool(mature_allowed),
        }

    def set_birth_date(self, value):
        iso_date = parse_birth_date(value)
        self.initialize()
        with self._connection() as db:
            db.execute("""UPDATE prime_age_preferences SET birth_date=?,
              updated_at=CURRENT_TIMESTAMP WHERE singleton=1""", (iso_date,))
            if age_years(iso_date) is None or age_years(iso_date) < 18:
                db.execute("""UPDATE watchlist_preferences SET mature=0,
                  updated_at=CURRENT_TIMESTAMP WHERE singleton=1""")
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
        result = evaluate_content(
            row, age=state.get("age"), mature_enabled=state.get("mature")
        )
        result.update({
            "age": state.get("age"),
            "birth_date": state.get("birth_date"),
            "mature": state.get("mature"),
            "mature_allowed": state.get("mature_allowed"),
        })
        return result
