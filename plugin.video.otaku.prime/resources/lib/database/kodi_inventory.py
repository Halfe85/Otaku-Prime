# -*- coding: utf-8 -*-
"""Snapshot Kodi's library and persist Prime ownership decisions."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)


class KodiInventoryStore:
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
            CREATE TABLE IF NOT EXISTS kodi_library_state(
              singleton INTEGER PRIMARY KEY CHECK(singleton=1),
              available INTEGER NOT NULL CHECK(available IN(0,1)),
              empty INTEGER NOT NULL CHECK(empty IN(0,1)),
              show_count INTEGER NOT NULL DEFAULT 0,
              episode_count INTEGER NOT NULL DEFAULT 0,
              last_error TEXT,scanned_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS kodi_inventory_shows(
              kodi_show_id INTEGER PRIMARY KEY,title TEXT,original_title TEXT,year INTEGER,
              path TEXT,unique_ids TEXT NOT NULL DEFAULT '{}',local_content INTEGER NOT NULL,
              scanned_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS kodi_inventory_episodes(
              kodi_episode_id INTEGER PRIMARY KEY,kodi_show_id INTEGER,title TEXT,
              show_title TEXT,season_number INTEGER,episode_number INTEGER,path TEXT,
              play_count INTEGER NOT NULL DEFAULT 0,last_played TEXT,
              unique_ids TEXT NOT NULL DEFAULT '{}',local_content INTEGER NOT NULL,
              scanned_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS kodi_media_ownership(
              prime_media_type TEXT NOT NULL,prime_local_id TEXT NOT NULL,
              kodi_show_id INTEGER,kodi_episode_id INTEGER,kodi_file_path TEXT,
              origin TEXT NOT NULL CHECK(origin IN('prime','existing_local','existing_plugin','missing')),
              ownership TEXT NOT NULL CHECK(ownership IN('prime','adopted','external','pending')),
              priority TEXT NOT NULL CHECK(priority IN('local','prime')),
              match_method TEXT NOT NULL,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(prime_media_type,prime_local_id));
            CREATE TABLE IF NOT EXISTS kodi_duplicate_candidates(
              id INTEGER PRIMARY KEY AUTOINCREMENT,prime_local_id TEXT NOT NULL,
              kodi_episode_id INTEGER NOT NULL,reason TEXT NOT NULL,
              resolution TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              UNIQUE(prime_local_id,kodi_episode_id));
            """)

    @staticmethod
    def _is_local(path):
        return not str(path or "").lower().startswith("plugin://")

    def replace_snapshot(self, shows, episodes, now=None):
        now = int(time.time() if now is None else now)
        shows = list(shows); episodes = list(episodes)
        with self._connection() as db:
            db.execute("DELETE FROM kodi_inventory_episodes")
            db.execute("DELETE FROM kodi_inventory_shows")
            for item in shows:
                db.execute("""INSERT INTO kodi_inventory_shows(
                  kodi_show_id,title,original_title,year,path,unique_ids,local_content,scanned_at)
                  VALUES(?,?,?,?,?,?,?,?)""", (
                    int(item["tvshowid"]),item.get("title"),item.get("originaltitle"),
                    item.get("year"),item.get("file"),json.dumps(item.get("uniqueid") or {},sort_keys=True),
                    int(self._is_local(item.get("file"))),now))
            for item in episodes:
                db.execute("""INSERT INTO kodi_inventory_episodes(
                  kodi_episode_id,kodi_show_id,title,show_title,season_number,episode_number,
                  path,play_count,last_played,unique_ids,local_content,scanned_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    int(item["episodeid"]),item.get("tvshowid"),item.get("title"),
                    item.get("showtitle"),item.get("season"),item.get("episode"),item.get("file"),
                    int(item.get("playcount") or 0),item.get("lastplayed"),
                    json.dumps(item.get("uniqueid") or {},sort_keys=True),
                    int(self._is_local(item.get("file"))),now))
            db.execute("""INSERT INTO kodi_library_state(
              singleton,available,empty,show_count,episode_count,last_error,scanned_at)
              VALUES(1,1,?,?,?,?,?) ON CONFLICT(singleton) DO UPDATE SET
              available=1,empty=excluded.empty,show_count=excluded.show_count,
              episode_count=excluded.episode_count,last_error=NULL,scanned_at=excluded.scanned_at""",
              (int(not shows and not episodes),len(shows),len(episodes),None,now))
        LOGGER.info("Stored Kodi inventory snapshot: %s shows, %s episodes",len(shows),len(episodes))
        return self.status()

    def mark_unavailable(self, error, now=None):
        now = int(time.time() if now is None else now)
        with self._connection() as db:
            db.execute("""INSERT INTO kodi_library_state(
              singleton,available,empty,show_count,episode_count,last_error,scanned_at)
              VALUES(1,0,1,0,0,?,?) ON CONFLICT(singleton) DO UPDATE SET
              available=0,last_error=excluded.last_error,scanned_at=excluded.scanned_at""",
              (str(error)[:1000],now))
        LOGGER.error("Kodi library marked unavailable: %s",error)

    def status(self):
        with self._connection() as db:
            row=db.execute("SELECT * FROM kodi_library_state WHERE singleton=1").fetchone()
            return dict(row) if row else {"available":False,"empty":True,
              "show_count":0,"episode_count":0,"last_error":None,"scanned_at":None}

    def shows(self):
        with self._connection() as db:
            values=[]
            for row in db.execute("SELECT * FROM kodi_inventory_shows"):
                item=dict(row); item["unique_ids"]=json.loads(item["unique_ids"]); values.append(item)
            return values

    def episodes(self):
        with self._connection() as db:
            values=[]
            for row in db.execute("SELECT * FROM kodi_inventory_episodes"):
                item=dict(row); item["unique_ids"]=json.loads(item["unique_ids"]); values.append(item)
            return values

    def resolution_targets(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT episode.local_id,
              episode.metadata_provider,episode.metadata_episode_id,
              episode.kodi_episode_number,season.kodi_season_number,
              series.metadata_show_id,series.metadata_provider AS show_provider
              FROM episodes AS episode
              JOIN seasons AS season ON season.local_id=episode.related_season_id
              JOIN tv_series AS series ON series.local_id=episode.related_series_id
              WHERE season.kodi_resolved=1 AND episode.metadata_episode_id IS NOT NULL""")]

    def save_ownership(self, target, match, origin, ownership, priority, method):
        with self._connection() as db:
            db.execute("""INSERT INTO kodi_media_ownership(
              prime_media_type,prime_local_id,kodi_show_id,kodi_episode_id,kodi_file_path,
              origin,ownership,priority,match_method) VALUES('episode',?,?,?,?,?,?,?,?)
              ON CONFLICT(prime_media_type,prime_local_id) DO UPDATE SET
              kodi_show_id=excluded.kodi_show_id,kodi_episode_id=excluded.kodi_episode_id,
              kodi_file_path=excluded.kodi_file_path,origin=excluded.origin,
              ownership=excluded.ownership,priority=excluded.priority,
              match_method=excluded.match_method,updated_at=CURRENT_TIMESTAMP""", (
                target["local_id"],match.get("kodi_show_id") if match else None,
                match.get("kodi_episode_id") if match else None,
                match.get("path") if match else None,origin,ownership,priority,method))

    def list_ownership(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute(
              "SELECT * FROM kodi_media_ownership ORDER BY prime_local_id")]
