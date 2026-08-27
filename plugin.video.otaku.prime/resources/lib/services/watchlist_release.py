# -*- coding: utf-8 -*-
"""Release schedule state owned by the Prime watchlist watchdog.

The canonical watchlist row stores two different release concepts:

* season_release_date: when this watchlist season/item begins releasing.
* next_episode_*: the next known unreleased catalogue episode and its date.

Prime local IDs remain the stable identity. Release scheduling is derived from
Prime's mediated catalogue, so provider IDs are never used as local keys here.
"""
from __future__ import annotations

import datetime
import hashlib
import re
import time


WATCHLIST_RELEASE_UPDATED = "WATCHLIST_RELEASE_UPDATED"
RELEASE_EVENT_FIELDS = (
    "season_release_date",
    "next_episode_local_id",
    "next_episode_number",
    "next_source_episode_number",
    "next_episode_release_date",
    "release_schedule_source",
)


def release_epoch(value):
    """Return the epoch used for release rollover.

    Exact timestamps are honoured exactly. A date-only value is considered
    released only after that UTC calendar day has finished, which prevents the
    watchdog from advancing at midnight before an episode may actually air.
    """
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 100000000000:
            number /= 1000.0
        return max(0, int(number))
    text = str(value).strip()
    if not text:
        return 0
    try:
        number = float(text)
        if number > 100000000000:
            number /= 1000.0
        return max(0, int(number))
    except ValueError:
        pass
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            parsed = datetime.datetime.strptime(text, "%Y-%m-%d").replace(
                hour=23,
                minute=59,
                second=59,
                tzinfo=datetime.timezone.utc,
            )
            return int(parsed.timestamp())
        except ValueError:
            return 0
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return max(0, int(parsed.timestamp()))
    except ValueError:
        return 0


class WatchlistReleaseManager:
    """Maintain season and next-episode dates on canonical watchlist rows."""

    def __init__(self, store):
        self.store = store

    def initialize(self):
        with self.store._connection() as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
            for column, declaration in (
                ("season_release_date", "TEXT"),
                ("season_release_epoch", "INTEGER NOT NULL DEFAULT 0"),
                ("next_episode_local_id", "TEXT"),
                ("next_episode_number", "INTEGER"),
                ("next_source_episode_number", "INTEGER"),
                ("next_episode_release_date", "TEXT"),
                ("next_episode_release_epoch", "INTEGER NOT NULL DEFAULT 0"),
                ("release_schedule_source", "TEXT"),
                ("release_schedule_checked_at", "TEXT"),
                ("release_catalog_updated_at", "TEXT"),
                ("release_catalog_signature", "TEXT"),
            ):
                if column not in columns:
                    db.execute(
                        "ALTER TABLE watchlist_items ADD COLUMN {} {}".format(
                            column, declaration
                        )
                    )

    @staticmethod
    def _table_exists(db, name):
        return bool(
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (str(name),),
            ).fetchone()
        )

    @staticmethod
    def _catalog_rows(db, local_id):
        if not WatchlistReleaseManager._table_exists(db, "seasons"):
            return None, []
        season = db.execute(
            "SELECT * FROM seasons WHERE watchlist_local_id=?",
            (str(local_id),),
        ).fetchone()
        if not season:
            return None, []
        if not WatchlistReleaseManager._table_exists(db, "episodes"):
            return season, []
        episodes = db.execute(
            "SELECT * FROM episodes WHERE related_season_id=? ORDER BY episode_number,local_id",
            (season["local_id"],),
        ).fetchall()
        return season, episodes

    @staticmethod
    def _catalog_updated_at(season, episodes):
        values = []
        if season and season["updated_at"]:
            values.append(str(season["updated_at"]))
        values.extend(str(row["updated_at"]) for row in episodes if row["updated_at"])
        return max(values) if values else None

    @staticmethod
    def _catalog_signature(season, episodes):
        if not season:
            return None
        parts = [
            str(season["local_id"]),
            str(season["release_date"] or ""),
            str(season["updated_at"] or ""),
        ]
        for row in episodes:
            parts.extend((
                str(row["local_id"]),
                str(row["episode_number"]),
                str(row["source_episode_number"]),
                str(row["release_date"] or ""),
                str(row["updated_at"] or ""),
            ))
        return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _schedule(item, season, episodes, now_epoch):
        dated = []
        for episode in episodes:
            value = episode["release_date"]
            epoch = release_epoch(value)
            if not epoch:
                continue
            dated.append((epoch, int(episode["episode_number"]), episode))
        dated.sort(key=lambda value: (value[0], value[1]))

        season_candidates = []
        if item.get("release_date"):
            season_candidates.append((release_epoch(item["release_date"]), item["release_date"]))
        if season and season["release_date"]:
            season_candidates.append((release_epoch(season["release_date"]), season["release_date"]))
        if dated:
            season_candidates.append((dated[0][0], dated[0][2]["release_date"]))
        season_candidates = [value for value in season_candidates if value[0] > 0]
        season_candidates.sort(key=lambda value: value[0])
        season_epoch, season_date = season_candidates[0] if season_candidates else (0, None)

        next_row = next((value for value in dated if value[0] > int(now_epoch)), None)
        if next_row:
            next_epoch, _, episode = next_row
            next_local_id = episode["local_id"]
            next_number = int(episode["episode_number"])
            next_source = int(episode["source_episode_number"])
            next_date = episode["release_date"]
        else:
            next_epoch = 0
            next_local_id = None
            next_number = None
            next_source = None
            next_date = None

        return {
            "season_release_date": season_date,
            "season_release_epoch": int(season_epoch or 0),
            "next_episode_local_id": next_local_id,
            "next_episode_number": next_number,
            "next_source_episode_number": next_source,
            "next_episode_release_date": next_date,
            "next_episode_release_epoch": int(next_epoch or 0),
            "release_schedule_source": "prime_catalog" if season else "watchlist",
            "release_catalog_updated_at": WatchlistReleaseManager._catalog_updated_at(
                season, episodes
            ),
            "release_catalog_signature": WatchlistReleaseManager._catalog_signature(
                season, episodes
            ),
        }

    @staticmethod
    def _changed(previous, current):
        return [
            field
            for field in RELEASE_EVENT_FIELDS
            if previous.get(field) != current.get(field)
        ]

    def _write_schedule(self, db, item, schedule):
        previous = dict(item)
        db.execute(
            """UPDATE watchlist_items SET
              season_release_date=?,season_release_epoch=?,next_episode_local_id=?,
              next_episode_number=?,next_source_episode_number=?,next_episode_release_date=?,
              next_episode_release_epoch=?,release_schedule_source=?,
              release_schedule_checked_at=CURRENT_TIMESTAMP,release_catalog_updated_at=?,
              release_catalog_signature=? WHERE local_id=?""",
            (
                schedule["season_release_date"],
                schedule["season_release_epoch"],
                schedule["next_episode_local_id"],
                schedule["next_episode_number"],
                schedule["next_source_episode_number"],
                schedule["next_episode_release_date"],
                schedule["next_episode_release_epoch"],
                schedule["release_schedule_source"],
                schedule["release_catalog_updated_at"],
                schedule["release_catalog_signature"],
                item["local_id"],
            ),
        )
        current = dict(
            db.execute(
                "SELECT * FROM watchlist_items WHERE local_id=?", (item["local_id"],)
            ).fetchone()
        )
        fields = self._changed(previous, current)
        if not fields:
            return None
        return {
            "local_id": current["local_id"],
            "previous": previous,
            "item": current,
            "changed_fields": fields,
        }

    def due_release_ids(self, now_epoch=None):
        """Return Prime items whose currently advertised next episode has released."""
        now_epoch = int(now_epoch if now_epoch is not None else time.time())
        with self.store._connection() as db:
            return [
                str(row["local_id"])
                for row in db.execute(
                    """SELECT local_id FROM watchlist_items
                    WHERE next_episode_release_epoch>0
                      AND next_episode_release_epoch<=?
                    ORDER BY next_episode_release_epoch,local_id""",
                    (now_epoch,),
                )
            ]

    def refresh_due(self, now_epoch=None, force=False):
        """Refresh schedules whose catalogue changed or whose next release passed."""
        now_epoch = int(now_epoch if now_epoch is not None else time.time())
        events = []
        with self.store._connection() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM watchlist_items")]
            for item in rows:
                season, episodes = self._catalog_rows(db, item["local_id"])
                catalog_signature = self._catalog_signature(season, episodes)
                due = bool(force or not item.get("release_schedule_checked_at"))
                if int(item.get("next_episode_release_epoch") or 0) > 0:
                    due = due or int(item["next_episode_release_epoch"]) <= now_epoch
                due = due or catalog_signature != item.get("release_catalog_signature")
                if not due:
                    continue
                schedule = self._schedule(item, season, episodes, now_epoch)
                event = self._write_schedule(db, item, schedule)
                if event:
                    events.append(event)
        return events
