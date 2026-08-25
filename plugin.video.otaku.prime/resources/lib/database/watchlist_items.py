# -*- coding: utf-8 -*-
"""Alpha9 canonical Prime watchlist and provider snapshot storage."""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager

SUPPORTED_WATCHLIST_PROVIDERS=("anilist","mal","kitsu","simkl")
ID_COLUMNS={provider:provider+"_id" for provider in SUPPORTED_WATCHLIST_PROVIDERS}
STATUSES=("CURRENT","COMPLETED","PAUSED","DROPPED","PLANNING")


class WatchlistIdentityConflict(ValueError):
    pass


class WatchlistItemStore:
    """Combine tracker snapshots into one Prime-owned row per anime identity."""
    def __init__(self,db_path): self.db_path=db_path

    @contextmanager
    def _connection(self):
        db=sqlite3.connect(self.db_path,timeout=10); db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON"); db.execute("PRAGMA journal_mode=WAL")
        try:
            with db: yield db
        finally: db.close()

    @staticmethod
    def _create_schema(db):
        db.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist_items(
          local_id TEXT PRIMARY KEY,
          anilist_id TEXT UNIQUE, mal_id TEXT UNIQUE,
          kitsu_id TEXT UNIQUE, simkl_id TEXT UNIQUE,
          english_name TEXT, romaji_name TEXT, native_name TEXT,
          status TEXT CHECK(status IN('CURRENT','COMPLETED','PAUSED','DROPPED','PLANNING')),
          progress INTEGER NOT NULL DEFAULT 0,
          episode_count INTEGER, media_format TEXT, release_date TEXT,
          is_adult INTEGER NOT NULL DEFAULT 0 CHECK(is_adult IN(0,1)),
          identity_resolution_status TEXT,
          identity_resolution_error TEXT,
          identity_checked_at TEXT,
          master_initialized INTEGER NOT NULL DEFAULT 0 CHECK(master_initialized IN(0,1)),
          has_conflict INTEGER NOT NULL DEFAULT 0 CHECK(has_conflict IN(0,1)),
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS watchlist_provider_entries(
          provider TEXT NOT NULL CHECK(provider IN('anilist','mal','kitsu','simkl')),
          provider_item_id TEXT NOT NULL,
          local_id TEXT NOT NULL,
          provider_status TEXT NOT NULL,
          status TEXT NOT NULL CHECK(status IN('CURRENT','COMPLETED','PAUSED','DROPPED','PLANNING')),
          progress INTEGER NOT NULL DEFAULT 0,
          episode_count INTEGER, media_format TEXT, release_date TEXT,
          is_adult INTEGER NOT NULL DEFAULT 0 CHECK(is_adult IN(0,1)),
          provider_updated_at TEXT, raw_json TEXT NOT NULL,
          fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(provider,provider_item_id),
          FOREIGN KEY(local_id) REFERENCES watchlist_items(local_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS ix_watchlist_provider_entries_local
          ON watchlist_provider_entries(local_id,provider);
        """)

    def initialize(self):
        with self._connection() as db:
            columns={row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
            legacy="provider" in columns and "local_id" not in columns
            if legacy:
                db.execute("ALTER TABLE watchlist_items RENAME TO watchlist_items_alpha8")
            self._create_schema(db)
            columns={row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
            for column,declaration in (
                ("identity_resolution_status","TEXT"),
                ("identity_resolution_error","TEXT"),
                ("identity_checked_at","TEXT"),
            ):
                if column not in columns:
                    db.execute("ALTER TABLE watchlist_items ADD COLUMN {} {}".format(column,declaration))
            if legacy:
                rows=db.execute("SELECT * FROM watchlist_items_alpha8").fetchall()
                for row in rows:
                    entry=dict(row); provider=entry["provider"]
                    entry["ids"]={provider:entry["provider_item_id"]}
                    entry["provider_status"]=entry["list_status"]
                    self._upsert_snapshot_row(db,provider,entry)
                db.execute("DROP TABLE watchlist_items_alpha8")
            for table in (
                "kodi_duplicate_candidates","kodi_media_ownership","kodi_inventory_episodes",
                "kodi_inventory_shows","kodi_library_state","kodi_episode_links",
                "kodi_movie_links","kodi_series_links","provider_watch_states",
                "watch_status_outbox","provider_list_entries","anilist_import_staging",
                "episodes","seasons","movies","tv_series","metadata_resolver_config",
                "watchlist_preferences",
            ): db.execute("DROP TABLE IF EXISTS "+table)

    @staticmethod
    def _clean_ids(provider,entry):
        ids={name:str(value) for name,value in (entry.get("ids") or {}).items()
             if name in ID_COLUMNS and value not in (None,"")}
        ids[provider]=str(entry["provider_item_id"])
        return ids

    @staticmethod
    def _matching_local_ids(db,ids):
        matches=set()
        for provider,value in ids.items():
            row=db.execute("SELECT local_id FROM watchlist_items WHERE {}=?".format(ID_COLUMNS[provider]),(value,)).fetchone()
            if row: matches.add(row[0])
        return sorted(matches)

    @staticmethod
    def _merge_items(db,target,duplicate):
        target_row=dict(db.execute("SELECT * FROM watchlist_items WHERE local_id=?",(target,)).fetchone())
        other=dict(db.execute("SELECT * FROM watchlist_items WHERE local_id=?",(duplicate,)).fetchone())
        for column in ("anilist_id","mal_id","kitsu_id","simkl_id"):
            if target_row.get(column) and other.get(column) and target_row[column]!=other[column]:
                raise WatchlistIdentityConflict(
                    "verified identity collision for {}: {} != {}".format(
                        column,target_row[column],other[column]))
        values={column:target_row.get(column) or other.get(column) for column in
                ("anilist_id","mal_id","kitsu_id","simkl_id","english_name","romaji_name","native_name",
                 "episode_count","media_format","release_date")}
        # Release the duplicate row's UNIQUE provider IDs before assigning them
        # to the surviving canonical row.
        db.execute("UPDATE OR REPLACE watchlist_provider_entries SET local_id=? WHERE local_id=?",(target,duplicate))
        db.execute("DELETE FROM watchlist_items WHERE local_id=?",(duplicate,))
        db.execute("""UPDATE watchlist_items SET anilist_id=?,mal_id=?,kitsu_id=?,simkl_id=?,
          english_name=?,romaji_name=?,native_name=?,episode_count=?,media_format=?,release_date=?,
          is_adult=MAX(is_adult,?),updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
          tuple(values[key] for key in ("anilist_id","mal_id","kitsu_id","simkl_id","english_name",
            "romaji_name","native_name","episode_count","media_format","release_date"))+(other["is_adult"],target))

    def _upsert_snapshot_row(self,db,provider,entry):
        ids=self._clean_ids(provider,entry); matches=self._matching_local_ids(db,ids)
        local_id=matches[0] if matches else uuid.uuid4().hex
        if not matches:
            db.execute("INSERT INTO watchlist_items(local_id) VALUES(?)",(local_id,))
        for duplicate in matches[1:]: self._merge_items(db,local_id,duplicate)
        assignments=[]; values=[]
        for name,value in ids.items(): assignments.append("{}=COALESCE({},?)".format(ID_COLUMNS[name],ID_COLUMNS[name])); values.append(value)
        for column in ("english_name","romaji_name","native_name","episode_count","media_format","release_date"):
            assignments.append("{}=COALESCE({},?)".format(column,column)); values.append(entry.get(column))
        assignments.extend(("is_adult=MAX(is_adult,?)","updated_at=CURRENT_TIMESTAMP")); values.append(int(bool(entry.get("is_adult"))))
        db.execute("UPDATE watchlist_items SET {} WHERE local_id=?".format(",".join(assignments)),tuple(values)+(local_id,))
        raw=entry.get("raw") if entry.get("raw") is not None else entry
        status=entry["list_status"]
        db.execute("""INSERT INTO watchlist_provider_entries(provider,provider_item_id,local_id,
          provider_status,status,progress,episode_count,media_format,release_date,is_adult,
          provider_updated_at,raw_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(provider,provider_item_id) DO UPDATE SET local_id=excluded.local_id,
          provider_status=excluded.provider_status,status=excluded.status,progress=excluded.progress,
          episode_count=excluded.episode_count,media_format=excluded.media_format,
          release_date=excluded.release_date,is_adult=excluded.is_adult,
          provider_updated_at=excluded.provider_updated_at,raw_json=excluded.raw_json,
          fetched_at=CURRENT_TIMESTAMP""",(provider,str(entry["provider_item_id"]),local_id,
          str(entry.get("provider_status") or status),status,max(0,int(entry.get("progress") or 0)),
          int(entry["episode_count"]) if entry.get("episode_count") is not None else None,
          entry.get("media_format"),entry.get("release_date"),int(bool(entry.get("is_adult"))),
          entry.get("provider_updated_at"),json.dumps(raw,ensure_ascii=False,separators=(",",":"))))
        return local_id

    def replace_provider_snapshot(self,provider,entries):
        provider=str(provider or "").lower()
        if provider not in SUPPORTED_WATCHLIST_PROVIDERS: raise ValueError("unsupported watchlist provider")
        rows=list({str(entry["provider_item_id"]):entry for entry in entries}.values()); ids=set()
        with self._connection() as db:
            for entry in rows: ids.add(str(entry["provider_item_id"])); self._upsert_snapshot_row(db,provider,entry)
            if ids:
                placeholders=",".join("?" for _ in ids)
                db.execute("DELETE FROM watchlist_provider_entries WHERE provider=? AND provider_item_id NOT IN ({})".format(placeholders),(provider,)+tuple(sorted(ids)))
            else: db.execute("DELETE FROM watchlist_provider_entries WHERE provider=?",(provider,))
            db.execute("DELETE FROM watchlist_items WHERE NOT EXISTS(SELECT 1 FROM watchlist_provider_entries e WHERE e.local_id=watchlist_items.local_id)")
        return len(rows)

    def finalize_merge(self):
        initialized=conflicts=0
        with self._connection() as db:
            items=db.execute("SELECT local_id,master_initialized,status,progress FROM watchlist_items").fetchall()
            for item in items:
                states=db.execute("SELECT status,progress FROM watchlist_provider_entries WHERE local_id=?",(item["local_id"],)).fetchall()
                unique={(row["status"],int(row["progress"])) for row in states}; conflict=len(unique)>1
                if not item["master_initialized"] and states:
                    chosen=max(states,key=lambda row:(int(row["progress"]),row["status"]=="COMPLETED",row["status"]=="CURRENT"))
                    db.execute("UPDATE watchlist_items SET status=?,progress=?,master_initialized=1,has_conflict=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?",
                      (chosen["status"],int(chosen["progress"]),int(conflict),item["local_id"])); initialized+=1
                else:
                    master=(item["status"],int(item["progress"])); conflict=any((row["status"],int(row["progress"]))!=master for row in states)
                    db.execute("UPDATE watchlist_items SET has_conflict=? WHERE local_id=?",(int(conflict),item["local_id"]))
                conflicts+=int(conflict)
        return {"initialized":initialized,"conflicts":conflicts,"items":len(items)}

    def list_provider(self,provider):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT entry.*,entry.status AS list_status,
              item.anilist_id,item.mal_id,item.kitsu_id,item.simkl_id,
              item.english_name,item.romaji_name,item.native_name FROM watchlist_provider_entries entry
              JOIN watchlist_items item ON item.local_id=entry.local_id WHERE entry.provider=?
              ORDER BY LOWER(COALESCE(item.english_name,item.romaji_name,item.native_name,''))""",(provider,))]

    def list_all(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT item.*,
              GROUP_CONCAT(entry.provider,',') AS connected_providers
              FROM watchlist_items item JOIN watchlist_provider_entries entry ON entry.local_id=item.local_id
              GROUP BY item.local_id ORDER BY LOWER(COALESCE(item.english_name,item.romaji_name,item.native_name,''))""")]

    def list_missing_provider_ids(self):
        """Return canonical items whose provider identity set is incomplete."""
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE (anilist_id IS NULL OR mal_id IS NULL OR kitsu_id IS NULL OR simkl_id IS NULL)
              AND COALESCE(identity_resolution_status,'PENDING')='PENDING'
              ORDER BY created_at,local_id""")]

    def record_identity_resolution(self,local_id,status,error=None):
        with self._connection() as db:
            cursor=db.execute("""UPDATE watchlist_items SET identity_resolution_status=?,
              identity_resolution_error=?,identity_checked_at=CURRENT_TIMESTAMP,
              updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (str(status),str(error) if error else None,local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")

    def apply_resolved_ids(self,local_id,ids):
        """Attach verified catalog IDs and merge rows they prove are identical."""
        clean={name:str(value) for name,value in (ids or {}).items()
               if name in ID_COLUMNS and value not in (None,"")}
        if not clean: return local_id
        with self._connection() as db:
            current=db.execute("SELECT * FROM watchlist_items WHERE local_id=?",(local_id,)).fetchone()
            if not current: raise KeyError("watchlist item not found")
            for provider,value in clean.items():
                existing=current[ID_COLUMNS[provider]]
                if existing and existing!=value:
                    raise WatchlistIdentityConflict(
                        "verified identity collision for {}: {} != {}".format(
                            ID_COLUMNS[provider],existing,value))
            matches=self._matching_local_ids(db,clean)
            for duplicate in matches:
                if duplicate!=local_id: self._merge_items(db,local_id,duplicate)
            assignments=[]; values=[]
            for provider,value in clean.items():
                column=ID_COLUMNS[provider]
                assignments.append("{}=COALESCE({},?)".format(column,column)); values.append(value)
            assignments.append("updated_at=CURRENT_TIMESTAMP")
            db.execute("UPDATE watchlist_items SET {} WHERE local_id=?".format(
                ",".join(assignments)),tuple(values)+(local_id,))
            row=db.execute("SELECT anilist_id,mal_id,kitsu_id,simkl_id FROM watchlist_items WHERE local_id=?",
                           (local_id,)).fetchone()
            status="RESOLVED" if all(row[column] for column in
                     ("anilist_id","mal_id","kitsu_id","simkl_id")) else "PARTIAL"
            db.execute("""UPDATE watchlist_items SET identity_resolution_status=?,
              identity_resolution_error=NULL,identity_checked_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (status,local_id))
        return local_id

    def set_master_state(self,local_id,status,progress):
        if status not in STATUSES: raise ValueError("unsupported watchlist status")
        with self._connection() as db:
            cursor=db.execute("UPDATE watchlist_items SET status=?,progress=?,master_initialized=1,updated_at=CURRENT_TIMESTAMP WHERE local_id=?",
              (status,max(0,int(progress)),local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")
        return self.finalize_merge()
