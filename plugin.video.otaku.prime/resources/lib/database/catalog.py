# -*- coding: utf-8 -*-
"""Alpha10 Prime catalogue: franchise -> watchlist season -> episode."""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager


HEX_SEGMENT_LENGTH=6


class CatalogStore:
    """Store opaque hierarchical catalogue identities in Prime's SQLite DB."""
    def __init__(self,db_path,segment_factory=None):
        self.db_path=db_path
        self._segment_factory=segment_factory or (lambda:secrets.token_hex(3))

    @contextmanager
    def _connection(self):
        db=sqlite3.connect(self.db_path,timeout=10); db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA journal_mode=WAL")
        try:
            with db: yield db
        finally: db.close()

    def initialize(self):
        with self._connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tv_series(
              local_id TEXT PRIMARY KEY
                CHECK(length(local_id)=6 AND local_id NOT GLOB '*[^0-9a-f]*'),
              english_name TEXT,
              romaji_name TEXT,
              root_simkl_id TEXT UNIQUE,
              tvdb_id TEXT UNIQUE,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS seasons(
              local_id TEXT PRIMARY KEY
                CHECK(length(local_id)=12 AND local_id NOT GLOB '*[^0-9a-f]*'),
              related_series_id TEXT NOT NULL,
              watchlist_local_id TEXT NOT NULL UNIQUE,
              anilist_id TEXT UNIQUE,
              mal_id TEXT UNIQUE,
              kitsu_id TEXT UNIQUE,
              simkl_id TEXT UNIQUE,
              season_number INTEGER CHECK(season_number IS NULL OR season_number>=0),
              provider_path TEXT,
              placement_source TEXT,
              first_episode INTEGER,
              last_episode INTEGER,
              english_name TEXT,
              romaji_name TEXT,
              media_format TEXT,
              release_date TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK(substr(local_id,1,6)=related_series_id),
              FOREIGN KEY(related_series_id) REFERENCES tv_series(local_id) ON DELETE CASCADE,
              FOREIGN KEY(watchlist_local_id) REFERENCES watchlist_items(local_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_seasons_series
              ON seasons(related_series_id,season_number);

            CREATE TABLE IF NOT EXISTS episodes(
              local_id TEXT PRIMARY KEY
                CHECK(length(local_id)=18 AND local_id NOT GLOB '*[^0-9a-f]*'),
              related_season_id TEXT NOT NULL,
              episode_number INTEGER NOT NULL CHECK(episode_number>0),
              source_episode_number INTEGER NOT NULL DEFAULT 1 CHECK(source_episode_number>0),
              mal_id TEXT,
              simkl_id TEXT UNIQUE,
              watch_status INTEGER NOT NULL DEFAULT 0 CHECK(watch_status IN(0,1)),
              release_date TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK(substr(local_id,1,12)=related_season_id),
              UNIQUE(related_season_id,episode_number),
              FOREIGN KEY(related_season_id) REFERENCES seasons(local_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_episodes_season
              ON episodes(related_season_id,episode_number);
            """)
            self._add_columns(db,"tv_series",(("tvdb_id","TEXT"),))
            self._add_columns(db,"seasons",(
                ("provider_path","TEXT"),("placement_source","TEXT"),
                ("first_episode","INTEGER"),("last_episode","INTEGER")))
            self._add_columns(db,"episodes",(("source_episode_number","INTEGER NOT NULL DEFAULT 1"),))
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_tv_series_tvdb
              ON tv_series(tvdb_id) WHERE tvdb_id IS NOT NULL""")

    @staticmethod
    def _add_columns(db,table,columns):
        existing={row[1] for row in db.execute("PRAGMA table_info({})".format(table))}
        for name,declaration in columns:
            if name not in existing:
                db.execute("ALTER TABLE {} ADD COLUMN {} {}".format(table,name,declaration))

    def _new_local_id(self,db,table,prefix=""):
        for _ in range(256):
            segment=str(self._segment_factory()).lower()
            if len(segment)!=HEX_SEGMENT_LENGTH or any(c not in "0123456789abcdef" for c in segment):
                raise ValueError("catalog ID factory must return exactly six hexadecimal characters")
            local_id=prefix+segment
            if not db.execute("SELECT 1 FROM {} WHERE local_id=?".format(table),(local_id,)).fetchone():
                return local_id
        raise RuntimeError("could not allocate a unique catalogue ID")

    def get_or_create_series(self,english_name=None,romaji_name=None,root_simkl_id=None,tvdb_id=None):
        root=str(root_simkl_id) if root_simkl_id not in (None,"") else None
        tvdb=str(tvdb_id) if tvdb_id not in (None,"") else None
        with self._connection() as db:
            row=None
            if root: row=db.execute("SELECT * FROM tv_series WHERE root_simkl_id=?",(root,)).fetchone()
            if not row and tvdb: row=db.execute("SELECT * FROM tv_series WHERE tvdb_id=?",(tvdb,)).fetchone()
            if row:
                db.execute("""UPDATE tv_series SET english_name=COALESCE(english_name,?),
                  romaji_name=COALESCE(romaji_name,?),root_simkl_id=COALESCE(root_simkl_id,?),
                  tvdb_id=COALESCE(tvdb_id,?),updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (english_name,romaji_name,root,tvdb,row["local_id"]))
                return dict(db.execute("SELECT * FROM tv_series WHERE local_id=?",(row["local_id"],)).fetchone())
            local_id=self._new_local_id(db,"tv_series")
            db.execute("""INSERT INTO tv_series(local_id,english_name,romaji_name,root_simkl_id,tvdb_id)
              VALUES(?,?,?,?,?)""",(local_id,english_name,romaji_name,root,tvdb))
            return dict(db.execute("SELECT * FROM tv_series WHERE local_id=?",(local_id,)).fetchone())

    def add_watchlist_season(self,series_id,watchlist_item,season_number=None,
                             provider_path=None,placement_source=None,
                             first_episode=None,last_episode=None):
        watchlist_id=str(watchlist_item["local_id"])
        with self._connection() as db:
            row=db.execute("SELECT * FROM seasons WHERE watchlist_local_id=?",(watchlist_id,)).fetchone()
            if row:
                db.execute("""UPDATE seasons SET season_number=?,provider_path=?,placement_source=?,
                  first_episode=?,last_episode=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (season_number,provider_path,placement_source,first_episode,last_episode,row["local_id"]))
                return dict(db.execute("SELECT * FROM seasons WHERE local_id=?",(row["local_id"],)).fetchone())
            if not db.execute("SELECT 1 FROM tv_series WHERE local_id=?",(series_id,)).fetchone():
                raise KeyError("TV series not found")
            local_id=self._new_local_id(db,"seasons",str(series_id))
            db.execute("""INSERT INTO seasons(local_id,related_series_id,watchlist_local_id,
              anilist_id,mal_id,kitsu_id,simkl_id,season_number,english_name,romaji_name,
              media_format,release_date,provider_path,placement_source,first_episode,last_episode)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (local_id,series_id,watchlist_id,watchlist_item.get("anilist_id"),
               watchlist_item.get("mal_id"),watchlist_item.get("kitsu_id"),
               watchlist_item.get("simkl_id"),season_number,watchlist_item.get("english_name"),
               watchlist_item.get("romaji_name"),watchlist_item.get("media_format"),
               watchlist_item.get("release_date"),provider_path,placement_source,
               first_episode,last_episode))
            return dict(db.execute("SELECT * FROM seasons WHERE local_id=?",(local_id,)).fetchone())

    def add_episode(self,season_id,episode_number,source_episode_number=None,mal_id=None,simkl_id=None,
                    watch_status=False,release_date=None):
        number=int(episode_number)
        with self._connection() as db:
            row=db.execute("SELECT * FROM episodes WHERE related_season_id=? AND episode_number=?",
                           (season_id,number)).fetchone()
            if row:
                db.execute("""UPDATE episodes SET source_episode_number=?,mal_id=COALESCE(mal_id,?),
                  simkl_id=COALESCE(simkl_id,?),release_date=COALESCE(release_date,?),
                  updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (int(source_episode_number or number),str(mal_id) if mal_id not in (None,"") else None,
                   str(simkl_id) if simkl_id not in (None,"") else None,release_date,row["local_id"]))
                return dict(db.execute("SELECT * FROM episodes WHERE local_id=?",(row["local_id"],)).fetchone())
            if not db.execute("SELECT 1 FROM seasons WHERE local_id=?",(season_id,)).fetchone():
                raise KeyError("season not found")
            local_id=self._new_local_id(db,"episodes",str(season_id))
            db.execute("""INSERT INTO episodes(local_id,related_season_id,episode_number,mal_id,
              simkl_id,watch_status,release_date,source_episode_number) VALUES(?,?,?,?,?,?,?,?)""",
              (local_id,season_id,number,str(mal_id) if mal_id not in (None,"") else None,
               str(simkl_id) if simkl_id not in (None,"") else None,
               int(bool(watch_status)),release_date,int(source_episode_number or number)))
            return dict(db.execute("SELECT * FROM episodes WHERE local_id=?",(local_id,)).fetchone())

    def list_series(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("SELECT * FROM tv_series ORDER BY local_id")]

    def list_seasons(self,series_id):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM seasons
              WHERE related_series_id=? ORDER BY season_number,local_id""",(series_id,))]

    def list_episodes(self,season_id):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM episodes
              WHERE related_season_id=? ORDER BY episode_number""",(season_id,))]

    def linked_watchlist_ids(self):
        with self._connection() as db:
            return {row[0] for row in db.execute("SELECT watchlist_local_id FROM seasons")}
