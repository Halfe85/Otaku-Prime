# -*- coding: utf-8 -*-
"""Franchise, season and episode identities plus boolean watch state."""
from __future__ import annotations
import secrets
import sqlite3
from contextlib import contextmanager

PROVIDERS = ("anilist", "mal", "kitsu", "simkl")
ID_COLUMNS = tuple("{}_id".format(p) for p in PROVIDERS)

class MediaIdentityConflict(ValueError):
    pass

def random_hex_id():
    return secrets.token_hex(16)

class WatchlistMediaStore:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self._connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tv_series(
              local_id TEXT PRIMARY KEY, english_name TEXT, romaji_name TEXT,
              anilist_root_id TEXT UNIQUE,franchise_resolved INTEGER NOT NULL DEFAULT 0,
              watched INTEGER NOT NULL DEFAULT 0 CHECK(watched IN(0,1)),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS seasons(
              local_id TEXT PRIMARY KEY, related_series_id TEXT NOT NULL,
              season_number INTEGER NOT NULL, english_name TEXT, romaji_name TEXT,
              anilist_id TEXT UNIQUE, mal_id TEXT UNIQUE, kitsu_id TEXT UNIQUE,
              simkl_id TEXT UNIQUE, release_date TEXT,
              media_format TEXT,relation_type TEXT,media_category TEXT,
              secondary_provider TEXT,secondary_id TEXT,
              kodi_show_name TEXT,kodi_show_year INTEGER,kodi_season_number INTEGER,
              kodi_resolved INTEGER NOT NULL DEFAULT 0,
              watched INTEGER NOT NULL DEFAULT 0 CHECK(watched IN(0,1)),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(related_series_id) REFERENCES tv_series(local_id) ON DELETE CASCADE,
              UNIQUE(related_series_id,season_number));
            CREATE TABLE IF NOT EXISTS episodes(
              local_id TEXT PRIMARY KEY, related_series_id TEXT NOT NULL,
              related_season_id TEXT NOT NULL, episode_number INTEGER NOT NULL,
              kodi_episode_number INTEGER,
              watched INTEGER NOT NULL DEFAULT 0 CHECK(watched IN(0,1)),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(related_series_id) REFERENCES tv_series(local_id) ON DELETE CASCADE,
              FOREIGN KEY(related_season_id) REFERENCES seasons(local_id) ON DELETE CASCADE,
              UNIQUE(related_season_id,episode_number));
            CREATE TABLE IF NOT EXISTS movies(
              local_id TEXT PRIMARY KEY, english_name TEXT, romaji_name TEXT,
              anilist_id TEXT UNIQUE, mal_id TEXT UNIQUE, kitsu_id TEXT UNIQUE,
              simkl_id TEXT UNIQUE, release_date TEXT, related_series_id TEXT,
              watched INTEGER NOT NULL DEFAULT 0 CHECK(watched IN(0,1)),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(related_series_id) REFERENCES tv_series(local_id) ON DELETE SET NULL);
            CREATE TABLE IF NOT EXISTS provider_watch_states(
              media_type TEXT NOT NULL, media_local_id TEXT NOT NULL,
              provider TEXT NOT NULL, watched INTEGER NOT NULL CHECK(watched IN(0,1)),
              source_updated_at TEXT, synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(media_type,media_local_id,provider));
            CREATE TABLE IF NOT EXISTS provider_list_entries(
              media_type TEXT NOT NULL,media_local_id TEXT NOT NULL,
              provider TEXT NOT NULL,list_status TEXT NOT NULL,
              synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(media_type,media_local_id,provider));
            CREATE TABLE IF NOT EXISTS anilist_import_staging(
              anilist_id TEXT PRIMARY KEY, english_name TEXT, romaji_name TEXT,
              list_status TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
              is_adult INTEGER NOT NULL DEFAULT 0 CHECK(is_adult IN(0,1)),
              media_format TEXT,release_date TEXT,
              synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS watch_status_outbox(
              id INTEGER PRIMARY KEY AUTOINCREMENT, media_type TEXT NOT NULL,
              media_local_id TEXT NOT NULL, provider TEXT NOT NULL,
              watched INTEGER NOT NULL CHECK(watched IN(0,1)), attempts INTEGER NOT NULL DEFAULT 0,
              last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              completed_at TEXT, UNIQUE(media_type,media_local_id,provider));
            CREATE TABLE IF NOT EXISTS kodi_series_links(
              series_local_id TEXT PRIMARY KEY, kodi_tvshow_id INTEGER NOT NULL UNIQUE,
              kodi_path TEXT, synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(series_local_id) REFERENCES tv_series(local_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS kodi_movie_links(
              movie_local_id TEXT PRIMARY KEY, kodi_movie_id INTEGER NOT NULL UNIQUE,
              kodi_file_id INTEGER UNIQUE, kodi_path TEXT,
              synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(movie_local_id) REFERENCES movies(local_id) ON DELETE CASCADE);
            CREATE TABLE IF NOT EXISTS kodi_episode_links(
              episode_local_id TEXT PRIMARY KEY, kodi_episode_id INTEGER NOT NULL UNIQUE,
              kodi_file_id INTEGER UNIQUE, kodi_path TEXT,
              synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(episode_local_id) REFERENCES episodes(local_id) ON DELETE CASCADE);
            """)

    def delete_empty_franchise(self, local_id):
        """Remove a superseded franchise only when nothing still owns it."""
        with self._connection() as db:
            cursor = db.execute("""DELETE FROM tv_series
              WHERE local_id=?
                AND NOT EXISTS(SELECT 1 FROM seasons WHERE related_series_id=?)
                AND NOT EXISTS(SELECT 1 FROM movies WHERE related_series_id=?)
                AND NOT EXISTS(SELECT 1 FROM watchlist_items WHERE franchise_local_id=?)""",
                (local_id, local_id, local_id, local_id))
            return cursor.rowcount == 1
            self._ensure_column(db,"tv_series","anilist_root_id","TEXT")
            self._ensure_column(db,"tv_series","franchise_resolved","INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db,"seasons","kodi_show_name","TEXT")
            self._ensure_column(db,"seasons","kodi_show_year","INTEGER")
            self._ensure_column(db,"seasons","kodi_season_number","INTEGER")
            self._ensure_column(db,"seasons","kodi_resolved","INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(db,"seasons","media_format","TEXT")
            self._ensure_column(db,"seasons","relation_type","TEXT")
            self._ensure_column(db,"seasons","media_category","TEXT")
            self._ensure_column(db,"seasons","secondary_provider","TEXT")
            self._ensure_column(db,"seasons","secondary_id","TEXT")
            self._ensure_column(db,"anilist_import_staging","media_format","TEXT")
            self._ensure_column(db,"anilist_import_staging","release_date","TEXT")
            self._ensure_column(db,"episodes","kodi_episode_number","INTEGER")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_tv_series_anilist_root ON tv_series(anilist_root_id)")

    @staticmethod
    def _ensure_column(db,table,column,definition):
        columns={row[1] for row in db.execute("PRAGMA table_info({})".format(table))}
        if column not in columns:
            db.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table,column,definition))

    @staticmethod
    def _external_ids(values):
        return {k: str(values[k]).strip() for k in ID_COLUMNS
                if values.get(k) is not None and str(values[k]).strip()}

    @staticmethod
    def _find(db, table, ids):
        matches = set()
        for column, value in ids.items():
            row = db.execute("SELECT local_id FROM {} WHERE {}=?".format(table, column),
                             (value,)).fetchone()
            if row:
                matches.add(row["local_id"])
        if len(matches) > 1:
            raise MediaIdentityConflict("provider IDs identify different records")
        return next(iter(matches)) if matches else None

    def upsert_tv_series(self, *, english_name=None, romaji_name=None, local_id=None,
                         anilist_root_id=None,franchise_resolved=False):
        if not (english_name or romaji_name or local_id):
            raise ValueError("a franchise title is required")
        with self._connection() as db:
            row = db.execute("""SELECT local_id FROM tv_series
              WHERE local_id=? OR (anilist_root_id IS NOT NULL AND anilist_root_id=?)
                 OR english_name=? OR romaji_name=?""",
                (local_id or "",str(anilist_root_id) if anilist_root_id else None,
                 english_name,romaji_name)).fetchone()
            value = row["local_id"] if row else random_hex_id()
            db.execute("""INSERT INTO tv_series(
              local_id,english_name,romaji_name,anilist_root_id,franchise_resolved)
              VALUES(?,?,?,?,?)
              ON CONFLICT(local_id) DO UPDATE SET
              english_name=COALESCE(excluded.english_name,english_name),
              romaji_name=COALESCE(excluded.romaji_name,romaji_name),
              anilist_root_id=COALESCE(excluded.anilist_root_id,anilist_root_id),
              franchise_resolved=MAX(franchise_resolved,excluded.franchise_resolved),
              updated_at=CURRENT_TIMESTAMP""", (
                value,english_name,romaji_name,
                str(anilist_root_id) if anilist_root_id else None,
                int(bool(franchise_resolved))))
            db.execute("UPDATE tv_series SET anilist_root_id=COALESCE(?,anilist_root_id),franchise_resolved=MAX(franchise_resolved,?) WHERE local_id=?",
                       (str(anilist_root_id) if anilist_root_id else None,int(bool(franchise_resolved)),value))
            return value

    def upsert_season(self, related_series_id, season_number, **media):
        ids = self._external_ids(media)
        with self._connection() as db:
            value = self._find(db, "seasons", ids)
            if not value:
                row = db.execute(
                    "SELECT local_id FROM seasons WHERE related_series_id=? AND season_number=?",
                    (related_series_id, int(season_number))).fetchone()
                value = row["local_id"] if row else "{}-{}".format(
                    related_series_id, random_hex_id())
            db.execute("INSERT OR IGNORE INTO seasons(local_id,related_series_id,season_number) VALUES(?,?,?)",
                       (value, related_series_id, int(season_number)))
            fields = {"english_name": media.get("english_name"),
                      "romaji_name": media.get("romaji_name"),
                      "release_date": media.get("release_date")}
            fields.update({key:media.get(key) for key in (
              "media_format","relation_type","media_category",
              "secondary_provider","secondary_id")})
            fields.update({key:media.get(key) for key in (
              "kodi_show_name","kodi_show_year","kodi_season_number","kodi_resolved")})
            fields.update(ids)
            self._update(db, "seasons", value, fields)
            return value

    def upsert_episode(self, related_season_id, episode_number, **media):
        with self._connection() as db:
            season = db.execute("SELECT related_series_id FROM seasons WHERE local_id=?",
                                (related_season_id,)).fetchone()
            if not season:
                raise KeyError("season does not exist")
            row = db.execute(
                "SELECT local_id FROM episodes WHERE related_season_id=? AND episode_number=?",
                (related_season_id, int(episode_number))).fetchone()
            token = related_season_id.rsplit("-", 1)[-1]
            value = row["local_id"] if row else "{}-{}-{}".format(
                season["related_series_id"], token, random_hex_id())
            db.execute("""INSERT OR IGNORE INTO episodes(
              local_id,related_series_id,related_season_id,episode_number) VALUES(?,?,?,?)""",
                       (value, season["related_series_id"], related_season_id,
                        int(episode_number)))
            return value

    def upsert_movie(self, **media):
        ids = self._external_ids(media)
        with self._connection() as db:
            value = self._find(db, "movies", ids) or random_hex_id()
            db.execute("INSERT OR IGNORE INTO movies(local_id) VALUES(?)", (value,))
            fields = {k: media.get(k) for k in
                      ("english_name", "romaji_name", "release_date", "related_series_id")}
            fields.update(ids)
            self._update(db, "movies", value, fields)
            return value

    def replace_anilist_staging(self, entries):
        """Atomically replace the authorized user's filtered AniList snapshot."""
        with self._connection() as db:
            db.execute("DELETE FROM anilist_import_staging")
            for entry in entries:
                db.execute("""INSERT INTO anilist_import_staging(
                  anilist_id,english_name,romaji_name,list_status,progress,is_adult,
                  media_format,release_date)
                  VALUES(?,?,?,?,?,?,?,?)
                  ON CONFLICT(anilist_id) DO UPDATE SET
                  english_name=excluded.english_name,romaji_name=excluded.romaji_name,
                  list_status=excluded.list_status,progress=excluded.progress,
                  is_adult=excluded.is_adult,media_format=excluded.media_format,
                  release_date=excluded.release_date,synced_at=CURRENT_TIMESTAMP""", (
                    str(entry["anilist_id"]),entry.get("english_name"),
                    entry.get("romaji_name"),entry["list_status"],
                    max(0,int(entry.get("progress") or 0)),
                    int(bool(entry.get("is_adult"))),entry.get("media_format"),
                    entry.get("release_date")))

    def list_anilist_staging(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM anilist_import_staging ORDER BY anilist_id")]

    def promote_anilist_season(self, entry, resolution):
        """Promote one staged list entry; relation-only media are never inserted."""
        root_id=str(resolution["root_id"])
        franchise=self.upsert_tv_series(
            english_name=resolution.get("franchise_english_name") or entry.get("english_name"),
            romaji_name=resolution.get("franchise_romaji_name") or entry.get("romaji_name"),
            anilist_root_id=root_id,franchise_resolved=True)
        anilist_id=str(entry["anilist_id"])
        category=resolution.get("media_category") or "tv"
        is_special=(category in ("movie","ova","oad","special","spin_off") or
                    (category=="ona" and resolution.get("relation_type") in
                     ("PARENT","SIDE_STORY","SPIN_OFF")))
        # Internal season numbers remain unique. Kodi placement is independently
        # represented by kodi_season_number, so specials can all target season 0.
        season_number=max(1,int(resolution.get("season_number") or 1))
        with self._connection() as db:
            existing=db.execute(
                "SELECT local_id,related_series_id FROM seasons WHERE anilist_id=?",
                (anilist_id,)).fetchone()
            occupied={int(row[0]) for row in db.execute(
                "SELECT season_number FROM seasons WHERE related_series_id=? AND local_id<>?",
                (franchise,existing["local_id"] if existing else ""))}
            while season_number in occupied:
                season_number+=1
            if existing:
                # Old builds created title-based placeholders. Re-parent them without
                # changing their opaque public ID, then remove an empty placeholder.
                old_series=existing["related_series_id"]
                db.execute("""UPDATE seasons SET related_series_id=?,season_number=?,
                  english_name=?,romaji_name=?,release_date=?,media_format=?,relation_type=?,
                  media_category=?,kodi_show_name=?,kodi_show_year=?,
                  kodi_season_number=?,kodi_resolved=1,updated_at=CURRENT_TIMESTAMP
                  WHERE local_id=?""", (
                    franchise,season_number,entry.get("english_name"),entry.get("romaji_name"),
                    entry.get("release_date"),resolution.get("media_format"),
                    resolution.get("relation_type"),category,
                    entry.get("english_name") or entry.get("romaji_name"),
                    resolution.get("start_year"),0 if is_special else season_number,
                    existing["local_id"]))
                db.execute("UPDATE episodes SET related_series_id=? WHERE related_season_id=?",
                           (franchise,existing["local_id"]))
                if old_series != franchise:
                    db.execute("""DELETE FROM tv_series WHERE local_id=? AND NOT EXISTS(
                      SELECT 1 FROM seasons WHERE related_series_id=?)""",(old_series,old_series))
                season=existing["local_id"]
            else:
                season=self.upsert_season(
                    franchise,season_number,english_name=entry.get("english_name"),
                    romaji_name=entry.get("romaji_name"),anilist_id=anilist_id,
                    release_date=entry.get("release_date"),
                    media_format=resolution.get("media_format"),
                    relation_type=resolution.get("relation_type"),media_category=category,
                    kodi_show_name=entry.get("english_name") or entry.get("romaji_name"),
                    kodi_show_year=resolution.get("start_year"),
                    kodi_season_number=0 if is_special else season_number,
                    # Specials need a verified TMDB/TVDB episode mapping before
                    # publishing, otherwise several AniList entries all become S00E01.
                    kodi_resolved=not is_special)
        progress=max(0,int(entry.get("progress") or 0))
        for number in range(1,progress+1):
            self.upsert_episode(season,number)
        self.import_provider_episode_count(season,"anilist",progress)
        self.save_provider_list_status(
            "season",season,"anilist",entry["list_status"])
        self.set_watch_status(
            "season",season,entry["list_status"]=="COMPLETED",
            source_provider="anilist",queue_connected_trackers=False)
        return season

    @staticmethod
    def _update(db, table, local_id, fields):
        fields = {k: v for k, v in fields.items() if v is not None}
        if fields:
            db.execute("UPDATE {} SET {},updated_at=CURRENT_TIMESTAMP WHERE local_id=?".format(
                table, ",".join("{}=?".format(k) for k in fields)),
                tuple(fields.values()) + (local_id,))

    def list_media(self, media_type):
        table = {"series": "tv_series", "season": "seasons",
                 "episode": "episodes", "movie": "movies"}.get(media_type)
        if not table:
            raise ValueError("invalid media_type")
        with self._connection() as db:
            return [dict(r) for r in db.execute("SELECT * FROM {}".format(table))]

    def list_tv_series_episodes(self, series_local_id):
        """Return episodes enriched with their parent season number."""
        with self._connection() as db:
            return [dict(row) for row in db.execute(
                """SELECT episode.*, season.season_number,season.kodi_season_number
                   FROM episodes AS episode
                   JOIN seasons AS season
                     ON season.local_id = episode.related_season_id
                   WHERE episode.related_series_id = ?
                   ORDER BY season.season_number, episode.episode_number""",
                (series_local_id,))]

    def list_watchlist_seasons(self):
        """Return provider-listed seasons for the administration interface."""
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT
              season.local_id,season.english_name,season.romaji_name,
              season.season_number,season.watched,season.release_date,
              season.media_format,season.relation_type,season.media_category,
              season.kodi_season_number,
              series.local_id AS series_local_id,
              COALESCE(series.english_name,series.romaji_name) AS franchise_name,
              entries.providers,entries.provider_statuses,
              COALESCE(progress.episode_count,0) AS episode_count,
              COALESCE(progress.watched_episodes,0) AS watched_episodes
              FROM seasons AS season
              JOIN tv_series AS series ON series.local_id=season.related_series_id
              JOIN (SELECT media_local_id,GROUP_CONCAT(provider) AS providers,
                GROUP_CONCAT(provider || ':' || list_status) AS provider_statuses
                FROM provider_list_entries WHERE media_type='season'
                GROUP BY media_local_id) AS entries ON entries.media_local_id=season.local_id
              LEFT JOIN (SELECT related_season_id,COUNT(*) AS episode_count,
                SUM(watched) AS watched_episodes FROM episodes GROUP BY related_season_id
              ) AS progress ON progress.related_season_id=season.local_id
              ORDER BY COALESCE(season.english_name,season.romaji_name),season.season_number""")]

    def set_watch_status(self, media_type, local_id, watched, *,
                         source_provider=None, queue_connected_trackers=True):
        table = {"series": "tv_series", "season": "seasons",
                 "episode": "episodes", "movie": "movies"}.get(media_type)
        if not table:
            raise ValueError("invalid media_type")
        value = int(bool(watched))
        with self._connection() as db:
            if db.execute("UPDATE {} SET watched=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?".format(table),
                          (value, local_id)).rowcount != 1:
                raise KeyError(local_id)
            if source_provider:
                db.execute("""INSERT INTO provider_watch_states(
                  media_type,media_local_id,provider,watched) VALUES(?,?,?,?)
                  ON CONFLICT(media_type,media_local_id,provider) DO UPDATE SET
                  watched=excluded.watched,synced_at=CURRENT_TIMESTAMP""",
                           (media_type, local_id, source_provider, value))
            if queue_connected_trackers:
                for row in db.execute("SELECT provider FROM watchlist_accounts WHERE user_id=1"):
                    if row[0] != source_provider:
                        db.execute("""INSERT INTO watch_status_outbox(
                          media_type,media_local_id,provider,watched) VALUES(?,?,?,?)
                          ON CONFLICT(media_type,media_local_id,provider) DO UPDATE SET
                          watched=excluded.watched,attempts=0,last_error=NULL,completed_at=NULL""",
                                   (media_type, local_id, row[0], value))

    def save_provider_list_status(self, media_type, local_id, provider, status):
        with self._connection() as db:
            db.execute("""INSERT INTO provider_list_entries(
              media_type,media_local_id,provider,list_status) VALUES(?,?,?,?)
              ON CONFLICT(media_type,media_local_id,provider) DO UPDATE SET
              list_status=excluded.list_status,synced_at=CURRENT_TIMESTAMP""",
                       (media_type,local_id,provider,status))

    def replace_provider_season_memberships(self, provider, season_ids):
        """Remove list memberships no longer present in the latest full import."""
        season_ids = set(season_ids)
        with self._connection() as db:
            rows = db.execute("""SELECT media_local_id FROM provider_list_entries
              WHERE media_type='season' AND provider=?""", (provider,)).fetchall()
            for row in rows:
                if row[0] not in season_ids:
                    db.execute("""DELETE FROM provider_list_entries
                      WHERE media_type='season' AND media_local_id=? AND provider=?""",
                               (row[0],provider))

    def import_provider_episode_count(self, season_id, provider, count, **_):
        with self._connection() as db:
            rows = db.execute(
                "SELECT local_id FROM episodes WHERE related_season_id=? ORDER BY episode_number",
                (season_id,)).fetchall()
            for pos, row in enumerate(rows, 1):
                watched = int(pos <= max(0, int(count)))
                db.execute("""INSERT INTO provider_watch_states(
                  media_type,media_local_id,provider,watched) VALUES('episode',?,?,?)
                  ON CONFLICT(media_type,media_local_id,provider) DO UPDATE SET
                  watched=excluded.watched,synced_at=CURRENT_TIMESTAMP""",
                           (row[0], provider, watched))
                db.execute("UPDATE episodes SET watched=? WHERE local_id=?",
                           (watched, row[0]))

    def pending_watch_updates(self, limit=100):
        with self._connection() as db:
            return [dict(r) for r in db.execute(
                "SELECT * FROM watch_status_outbox WHERE completed_at IS NULL ORDER BY id LIMIT ?",
                (limit,))]
    def complete_watch_update(self, update_id):
        with self._connection() as db:
            db.execute("UPDATE watch_status_outbox SET completed_at=CURRENT_TIMESTAMP,last_error=NULL WHERE id=?",
                       (update_id,))
    def fail_watch_update(self, update_id, error):
        with self._connection() as db:
            db.execute("UPDATE watch_status_outbox SET attempts=attempts+1,last_error=? WHERE id=?",
                       (str(error)[:1000], update_id))

    def link_kodi(self, media_type, local_id, kodi_id, *, kodi_file_id=None, kodi_path=None):
        defs = {"series": ("kodi_series_links", "series_local_id", "kodi_tvshow_id"),
                "movie": ("kodi_movie_links", "movie_local_id", "kodi_movie_id"),
                "episode": ("kodi_episode_links", "episode_local_id", "kodi_episode_id")}
        if media_type not in defs:
            raise ValueError("invalid media_type")
        table, key, kodi_key = defs[media_type]
        columns, values = [key, kodi_key], [local_id, int(kodi_id)]
        if media_type != "series":
            columns.append("kodi_file_id"); values.append(kodi_file_id)
        columns.append("kodi_path"); values.append(kodi_path)
        updates = ",".join("{}=excluded.{}".format(c, c) for c in columns[1:])
        with self._connection() as db:
            db.execute("INSERT INTO {}({}) VALUES({}) ON CONFLICT({}) DO UPDATE SET {}".format(
                table, ",".join(columns), ",".join("?" for _ in columns), key, updates),
                tuple(values))

    def get_kodi_link(self, media_type, local_id):
        defs = {"series": ("kodi_series_links", "series_local_id"),
                "episode": ("kodi_episode_links", "episode_local_id"),
                "movie": ("kodi_movie_links", "movie_local_id")}
        if media_type not in defs:
            raise ValueError("invalid media_type")
        table, key = defs[media_type]
        with self._connection() as db:
            row = db.execute("SELECT * FROM {} WHERE {}=?".format(table, key),
                             (local_id,)).fetchone()
            return dict(row) if row else None
