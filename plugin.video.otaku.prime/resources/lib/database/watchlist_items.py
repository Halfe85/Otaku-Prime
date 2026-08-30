# -*- coding: utf-8 -*-
"""Canonical Prime watchlist and provider snapshot storage."""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager

from resources.lib.services.remote_identity import clean_remote_text

SUPPORTED_WATCHLIST_PROVIDERS=("anilist","mal","kitsu","simkl")
ID_COLUMNS={provider:provider+"_id" for provider in SUPPORTED_WATCHLIST_PROVIDERS}
STATUSES=("CURRENT","COMPLETED","PAUSED","DROPPED","PLANNING")
SPECIAL_LOCATOR_RE=re.compile(r"^S(\d{2,3})E(\d{2,4})(?:-E?(\d{2,4}))?$")


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
          simkl_reference_id TEXT,
          special_locator TEXT,
          english_name TEXT, preferred_name TEXT, romaji_name TEXT, native_name TEXT,
          alternative_titles_json TEXT NOT NULL DEFAULT '[]',
          status TEXT CHECK(status IN('CURRENT','COMPLETED','PAUSED','DROPPED','PLANNING')),
          progress INTEGER NOT NULL DEFAULT 0,
          episode_count INTEGER, media_format TEXT, release_date TEXT,
          is_adult INTEGER NOT NULL DEFAULT 0 CHECK(is_adult IN(0,1)),
          identity_resolution_status TEXT,
          identity_resolution_error TEXT,
          identity_checked_at TEXT,
          identity_resolution_version INTEGER NOT NULL DEFAULT 3,
          mediator_ready INTEGER NOT NULL DEFAULT 0 CHECK(mediator_ready IN(0,1)),
          added_to_library INTEGER NOT NULL DEFAULT 0 CHECK(added_to_library IN(0,1)),
          library_added_at TEXT,
          mediator_status TEXT,
          mediator_provider TEXT,
          mediator_error TEXT,
          mediator_checked_at TEXT,
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
        CREATE TABLE IF NOT EXISTS watchlist_preferences(
          singleton INTEGER PRIMARY KEY CHECK(singleton=1),
          mature INTEGER NOT NULL DEFAULT 0 CHECK(mature IN(0,1)),
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO watchlist_preferences(singleton,mature) VALUES(1,0);
        """)

    def initialize(self):
        with self._connection() as db:
            columns={row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
            legacy="provider" in columns and "local_id" not in columns
            if legacy:
                db.execute("ALTER TABLE watchlist_items RENAME TO watchlist_items_alpha8")
            self._create_schema(db)
            columns={row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}
            if "identity_resolution_version" not in columns:
                db.execute("""ALTER TABLE watchlist_items ADD COLUMN
                  identity_resolution_version INTEGER NOT NULL DEFAULT 1""")
            for column,declaration in (
                ("identity_resolution_status","TEXT"),
                ("identity_resolution_error","TEXT"),
                ("identity_checked_at","TEXT"),
                ("simkl_reference_id","TEXT"),
                ("special_locator","TEXT"),
                ("preferred_name","TEXT"),
                ("alternative_titles_json","TEXT NOT NULL DEFAULT '[]'"),
                ("mediator_ready","INTEGER NOT NULL DEFAULT 0 CHECK(mediator_ready IN(0,1))"),
                ("added_to_library","INTEGER NOT NULL DEFAULT 0 CHECK(added_to_library IN(0,1))"),
                ("library_added_at","TEXT"),
                ("mediator_status","TEXT"),
                ("mediator_provider","TEXT"),
                ("mediator_error","TEXT"),
                ("mediator_checked_at","TEXT"),
            ):
                if column not in columns:
                    db.execute("ALTER TABLE watchlist_items ADD COLUMN {} {}".format(column,declaration))
            db.execute("""CREATE INDEX IF NOT EXISTS ix_watchlist_mediator_queue
              ON watchlist_items(added_to_library,mediator_ready)""")
            db.execute("""UPDATE watchlist_items SET identity_resolution_status='PENDING',
              identity_resolution_error=NULL,identity_resolution_version=3
              WHERE identity_resolution_version<3""")
            if legacy:
                rows=db.execute("SELECT * FROM watchlist_items_alpha8").fetchall()
                for row in rows:
                    entry=dict(row); provider=entry["provider"]
                    entry["ids"]={provider:entry["provider_item_id"]}
                    entry["provider_status"]=entry["list_status"]
                    self._upsert_snapshot_row(db,provider,entry)
                db.execute("DROP TABLE watchlist_items_alpha8")
            table=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='seasons'").fetchone()
            if table:
                season_columns={row[1] for row in db.execute("PRAGMA table_info(seasons)")}
                placement_filter=(
                    " AND COALESCE(s.placement_state,'COMPLETE')='COMPLETE'"
                    if "placement_state" in season_columns else "")
                db.execute("""UPDATE watchlist_items SET added_to_library=1,mediator_ready=0,
                  library_added_at=COALESCE(library_added_at,CURRENT_TIMESTAMP)
                    WHERE EXISTS(SELECT 1 FROM seasons s
                    WHERE s.watchlist_local_id=watchlist_items.local_id{})""".format(
                        placement_filter))
            movie_table=db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='movies'"
            ).fetchone()
            if movie_table:
                db.execute("""UPDATE watchlist_items SET added_to_library=1,mediator_ready=0,
                  library_added_at=COALESCE(library_added_at,CURRENT_TIMESTAMP)
                  WHERE EXISTS(SELECT 1 FROM movies
                    WHERE movies.watchlist_local_id=watchlist_items.local_id)""")
            for table_name in (
                "kodi_duplicate_candidates","kodi_media_ownership","kodi_inventory_episodes",
                "kodi_inventory_shows","kodi_library_state","kodi_episode_links",
                "kodi_movie_links","kodi_series_links","provider_watch_states",
                "watch_status_outbox","provider_list_entries","anilist_import_staging",
                "metadata_resolver_config",
            ): db.execute("DROP TABLE IF EXISTS "+table_name)
            self._repair_encoded_text(db)

    def preferences(self):
        """Return the one-user watchlist policy as primitive JSON-safe values."""
        with self._connection() as db:
            row=db.execute(
                "SELECT mature FROM watchlist_preferences WHERE singleton=1"
            ).fetchone()
            return {"mature":int(row["mature"] if row else 0)}

    def set_mature(self,value):
        """Persist the 18+ policy exactly as 0 (disabled) or 1 (enabled)."""
        if isinstance(value,str):
            text=value.strip().lower()
            if text not in ("0","1","false","true","off","on","no","yes"):
                raise ValueError("mature must be 0 or 1")
            mature=1 if text in ("1","true","on","yes") else 0
        elif value in (0,1,False,True):
            mature=int(bool(value))
        else:
            raise ValueError("mature must be 0 or 1")
        with self._connection() as db:
            db.execute("""INSERT INTO watchlist_preferences(singleton,mature)
              VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET
              mature=excluded.mature,updated_at=CURRENT_TIMESTAMP""",(mature,))
            if mature:
                # Releasing all adult rows lets identity enrichment decide which
                # provider path is usable.  Existing completed library rows stay
                # linked and are never duplicated.
                db.execute("""UPDATE watchlist_items SET mediator_ready=CASE
                  WHEN added_to_library=0 THEN 1 ELSE 0 END,
                  updated_at=CURRENT_TIMESTAMP WHERE is_adult=1""")
            else:
                db.execute("""UPDATE watchlist_items SET mediator_ready=0,
                  updated_at=CURRENT_TIMESTAMP
                  WHERE is_adult=1 AND added_to_library=0""")
        return {"mature":mature}

    @staticmethod
    def _repair_encoded_text(db):
        columns=("english_name","preferred_name","romaji_name","native_name")
        for row in db.execute(
            "SELECT local_id,english_name,preferred_name,romaji_name,native_name,"
            "alternative_titles_json FROM watchlist_items"
        ).fetchall():
            values={name:clean_remote_text(row[name]) for name in columns}
            alternatives=WatchlistItemStore._encode_alternative_titles(
                row["alternative_titles_json"])
            if (any(values[name]!=row[name] for name in columns) or
                    alternatives!=(row["alternative_titles_json"] or "[]")):
                db.execute("""UPDATE watchlist_items SET english_name=?,preferred_name=?,
                  romaji_name=?,native_name=?,alternative_titles_json=?,
                  updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  tuple(values[name] for name in columns)+(alternatives,row["local_id"]))

    @staticmethod
    def _alternative_titles(value):
        if value in (None,""):
            return []
        if isinstance(value,str):
            try:
                decoded=json.loads(value)
            except (TypeError,ValueError,json.JSONDecodeError):
                decoded=[value]
        else:
            decoded=value
        if not isinstance(decoded,(list,tuple,set)):
            decoded=[decoded]
        result=[]; seen=set()
        for item in decoded:
            title=clean_remote_text(item)
            title=str(title).strip() if title not in (None,"") else ""
            key=title.casefold()
            if title and key not in seen:
                result.append(title); seen.add(key)
        return result

    @classmethod
    def _encode_alternative_titles(cls,value):
        return json.dumps(cls._alternative_titles(value),ensure_ascii=False,separators=(",",":"))

    @classmethod
    def _merge_alternative_titles(cls,*values):
        combined=[]
        for value in values:
            combined.extend(cls._alternative_titles(value))
        return cls._encode_alternative_titles(combined)

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
                    "verified identity collision for {}: {} != {}".format(column,target_row[column],other[column]))
        values={column:target_row.get(column) or other.get(column) for column in
                ("anilist_id","mal_id","kitsu_id","simkl_id","simkl_reference_id","special_locator",
                 "english_name","preferred_name","romaji_name","native_name","episode_count",
                 "media_format","release_date")}
        alternatives=WatchlistItemStore._merge_alternative_titles(
            target_row.get("alternative_titles_json"),other.get("alternative_titles_json"))
        added=max(int(target_row.get("added_to_library") or 0),int(other.get("added_to_library") or 0))
        ready=0 if added else max(int(target_row.get("mediator_ready") or 0),int(other.get("mediator_ready") or 0))
        db.execute("UPDATE OR REPLACE watchlist_provider_entries SET local_id=? WHERE local_id=?",(target,duplicate))
        db.execute("DELETE FROM watchlist_items WHERE local_id=?",(duplicate,))
        db.execute("""UPDATE watchlist_items SET anilist_id=?,mal_id=?,kitsu_id=?,simkl_id=?,
          simkl_reference_id=?,special_locator=?,english_name=?,preferred_name=?,romaji_name=?,native_name=?,
          alternative_titles_json=?,
          episode_count=?,media_format=?,release_date=?,is_adult=MAX(is_adult,?),
          added_to_library=?,mediator_ready=?,updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
          tuple(values[key] for key in ("anilist_id","mal_id","kitsu_id","simkl_id","simkl_reference_id",
            "special_locator","english_name","preferred_name","romaji_name","native_name"))+
            (alternatives,)+tuple(values[key] for key in ("episode_count","media_format",
            "release_date"))+(other["is_adult"],added,ready,target))

    def _upsert_snapshot_row(self,db,provider,entry):
        ids=self._clean_ids(provider,entry); matches=self._matching_local_ids(db,ids)
        local_id=matches[0] if matches else uuid.uuid4().hex
        if not matches:
            db.execute("INSERT INTO watchlist_items(local_id) VALUES(?)",(local_id,))
        for duplicate in matches[1:]: self._merge_items(db,local_id,duplicate)
        assignments=[]; values=[]
        for name,value in ids.items():
            assignments.append("{}=COALESCE({},?)".format(ID_COLUMNS[name],ID_COLUMNS[name])); values.append(value)
        for column in ("english_name","preferred_name","romaji_name","native_name",
                       "episode_count","media_format","release_date"):
            value=entry.get(column)
            if column in ("english_name","preferred_name","romaji_name","native_name"):
                value=clean_remote_text(value)
            assignments.append("{}=COALESCE({},?)".format(column,column)); values.append(value)
        if "alternative_titles" in entry:
            current=db.execute("SELECT alternative_titles_json FROM watchlist_items WHERE local_id=?",
                               (local_id,)).fetchone()
            alternatives=self._merge_alternative_titles(
                current[0] if current else None,entry.get("alternative_titles"))
            assignments.append("alternative_titles_json=?"); values.append(alternatives)
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
            for entry in rows:
                ids.add(str(entry["provider_item_id"])); self._upsert_snapshot_row(db,provider,entry)
            if ids:
                placeholders=",".join("?" for _ in ids)
                db.execute("DELETE FROM watchlist_provider_entries WHERE provider=? AND provider_item_id NOT IN ({})".format(placeholders),(provider,)+tuple(sorted(ids)))
            else:
                db.execute("DELETE FROM watchlist_provider_entries WHERE provider=?",(provider,))
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
                    master=(item["status"],int(item["progress"] or 0)); conflict=any((row["status"],int(row["progress"]))!=master for row in states)
                    db.execute("UPDATE watchlist_items SET has_conflict=? WHERE local_id=?",(int(conflict),item["local_id"]))
                conflicts+=int(conflict)
        return {"initialized":initialized,"conflicts":conflicts,"items":len(items)}

    @staticmethod
    def _order_clause():
        return "CASE WHEN COALESCE(english_name,preferred_name,romaji_name,native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END, LOWER(COALESCE(english_name,preferred_name,romaji_name,native_name,'')),local_id"

    def list_provider(self,provider):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT entry.*,entry.status AS list_status,
              item.anilist_id,item.mal_id,item.kitsu_id,item.simkl_id,item.simkl_reference_id,item.special_locator,
              item.english_name,item.preferred_name,item.romaji_name,item.native_name,
              item.alternative_titles_json,item.added_to_library,item.mediator_ready
              FROM watchlist_provider_entries entry JOIN watchlist_items item ON item.local_id=entry.local_id
              WHERE entry.provider=? ORDER BY LOWER(COALESCE(item.english_name,item.preferred_name,
              item.romaji_name,item.native_name,''))""",(provider,))]

    def list_all(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT item.*,
              GROUP_CONCAT(entry.provider,',') AS connected_providers
              FROM watchlist_items item JOIN watchlist_provider_entries entry ON entry.local_id=item.local_id
              GROUP BY item.local_id ORDER BY
              CASE WHEN COALESCE(item.english_name,item.preferred_name,item.romaji_name,item.native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(item.english_name,item.preferred_name,item.romaji_name,item.native_name,'')),item.local_id""")]

    def list_ui_items(self):
        """Return only fields used by Watchlist Management.

        Background bookkeeping must not be serialized into the browser on each
        page load. The complete rows remain available to watchdog services via
        ``list_all``.
        """
        with self._connection() as db:
            result=[dict(row) for row in db.execute("""SELECT
              item.local_id,item.anilist_id,item.mal_id,item.kitsu_id,item.simkl_id,
              item.simkl_reference_id,item.special_locator,
              item.english_name,item.preferred_name,item.romaji_name,item.native_name,
              item.alternative_titles_json,
              item.status,item.progress,item.episode_count,item.media_format,item.release_date,
              item.is_adult,
              item.has_conflict,item.identity_resolution_status,item.identity_resolution_error,
              item.mediator_status,item.mediator_provider,item.mediator_error,
              item.mediator_ready,item.added_to_library,
              GROUP_CONCAT(entry.provider,',') AS connected_providers
              FROM watchlist_items item
              JOIN watchlist_provider_entries entry ON entry.local_id=item.local_id
              WHERE item.is_adult=0 OR COALESCE((SELECT mature FROM
                watchlist_preferences WHERE singleton=1),0)=1
              GROUP BY item.local_id ORDER BY
              CASE WHEN COALESCE(item.english_name,item.preferred_name,item.romaji_name,item.native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(item.english_name,item.preferred_name,item.romaji_name,item.native_name,'')),item.local_id""")]
            for item in result:
                item["alternative_titles"]=self._alternative_titles(
                    item.pop("alternative_titles_json",None))
            return result

    def list_ui_library_states(self):
        """Return the small mediator-state delta polled by the visible UI tab."""
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT local_id,
              added_to_library,mediator_ready,mediator_status,mediator_provider,
              anilist_id,mal_id,kitsu_id,simkl_id,simkl_reference_id,special_locator,
              identity_resolution_status,identity_resolution_error
              FROM watchlist_items ORDER BY local_id""")]

    def item(self,local_id):
        with self._connection() as db:
            row=db.execute("SELECT * FROM watchlist_items WHERE local_id=?",(str(local_id),)).fetchone()
            return dict(row) if row else None

    def list_missing_provider_ids(self):
        """Return retryable incomplete identities in deterministic # -> Z order."""
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE (anilist_id IS NULL OR mal_id IS NULL OR kitsu_id IS NULL OR
                     (simkl_id IS NULL AND simkl_reference_id IS NULL))
              AND COALESCE(identity_resolution_status,'PENDING') NOT IN('CONFLICT_EXACT')
              ORDER BY CASE WHEN COALESCE(english_name,romaji_name,native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(english_name,romaji_name,native_name,'')),local_id""")]

    def list_watchdog_work(self):
        """Rows the watchdog must identity-check or release to the mediator."""
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE (is_adult=0 OR COALESCE((SELECT mature FROM
                watchlist_preferences WHERE singleton=1),0)=1) AND
              (added_to_library=0 OR (
                (anilist_id IS NULL OR mal_id IS NULL OR kitsu_id IS NULL OR
                 (simkl_id IS NULL AND simkl_reference_id IS NULL))
                AND COALESCE(identity_resolution_status,'PENDING') NOT IN('CONFLICT_EXACT')))
              ORDER BY CASE WHEN COALESCE(english_name,romaji_name,native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(english_name,romaji_name,native_name,'')),local_id""")]

    def list_mediator_ready(self):
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM watchlist_items
              WHERE mediator_ready=1 AND added_to_library=0
              AND (is_adult=0 OR COALESCE((SELECT mature FROM
                watchlist_preferences WHERE singleton=1),0)=1)
              ORDER BY CASE WHEN COALESCE(english_name,romaji_name,native_name,'') GLOB '[A-Za-z]*' THEN 1 ELSE 0 END,
              LOWER(COALESCE(english_name,romaji_name,native_name,'')),local_id""")]

    def record_identity_resolution(self,local_id,status,error=None):
        with self._connection() as db:
            cursor=db.execute("""UPDATE watchlist_items SET identity_resolution_status=?,
              identity_resolution_error=?,identity_checked_at=CURRENT_TIMESTAMP,
              identity_resolution_version=3,updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (str(status),str(error) if error else None,local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")

    def apply_resolved_ids(self,local_id,ids):
        """Attach verified catalogue IDs and merge rows they prove are identical."""
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
                        "verified identity collision for {}: {} != {}".format(ID_COLUMNS[provider],existing,value))
            matches=self._matching_local_ids(db,clean)
            for duplicate in matches:
                if duplicate!=local_id: self._merge_items(db,local_id,duplicate)
            assignments=[]; values=[]
            for provider,value in clean.items():
                column=ID_COLUMNS[provider]
                assignments.append("{}=COALESCE({},?)".format(column,column)); values.append(value)
            assignments.append("updated_at=CURRENT_TIMESTAMP")
            db.execute("UPDATE watchlist_items SET {} WHERE local_id=?".format(",".join(assignments)),tuple(values)+(local_id,))
            row=db.execute("""SELECT anilist_id,mal_id,kitsu_id,simkl_id,simkl_reference_id
              FROM watchlist_items WHERE local_id=?""",(local_id,)).fetchone()
            complete=bool(row["anilist_id"] and row["mal_id"] and row["kitsu_id"] and
                          (row["simkl_id"] or row["simkl_reference_id"]))
            db.execute("""UPDATE watchlist_items SET identity_resolution_status=?,
              identity_resolution_error=NULL,identity_checked_at=CURRENT_TIMESTAMP,
              identity_resolution_version=3 WHERE local_id=?""",("RESOLVED" if complete else "PARTIAL",local_id))
        return local_id

    def set_special_reference(self,local_id,simkl_reference_id,special_locator):
        reference=str(simkl_reference_id or "").strip()
        locator=str(special_locator or "").upper().strip()
        if not reference: raise ValueError("Simkl special reference ID is required")
        match=SPECIAL_LOCATOR_RE.match(locator)
        if not match: raise ValueError("special locator must look like S00E08 or S00E08-E09")
        if match.group(3) and int(match.group(3))<int(match.group(2)):
            raise ValueError("special locator range must run forwards")
        with self._connection() as db:
            cursor=db.execute("""UPDATE watchlist_items SET simkl_reference_id=?,special_locator=?,
              identity_resolution_status=CASE WHEN identity_resolution_status='RESOLVED' THEN 'RESOLVED' ELSE 'PARTIAL' END,
              identity_resolution_error=NULL,identity_checked_at=CURRENT_TIMESTAMP,
              identity_resolution_version=3,updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (reference,locator,local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")
        return local_id

    def mark_mediator_ready(self,local_id,ready=True):
        with self._connection() as db:
            cursor=db.execute("""UPDATE watchlist_items SET mediator_ready=CASE WHEN added_to_library=1 THEN 0 ELSE ? END,
              updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",(int(bool(ready)),local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")

    def mark_added_to_library(self,local_id,provider=None):
        with self._connection() as db:
            cursor=db.execute("""UPDATE watchlist_items SET added_to_library=1,mediator_ready=0,
              library_added_at=COALESCE(library_added_at,CURRENT_TIMESTAMP),mediator_status='RESOLVED',
              mediator_provider=COALESCE(?,mediator_provider),mediator_error=NULL,
              mediator_checked_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (str(provider) if provider else None,local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")

    def clear_mediator_ready(self,local_id):
        self.mark_mediator_ready(local_id,False)

    def set_master_state(self,local_id,status,progress):
        if status not in STATUSES: raise ValueError("unsupported watchlist status")
        with self._connection() as db:
            cursor=db.execute("UPDATE watchlist_items SET status=?,progress=?,master_initialized=1,updated_at=CURRENT_TIMESTAMP WHERE local_id=?",
              (status,max(0,int(progress)),local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")
        return self.finalize_merge()

    def record_mediator_resolution(self,local_id,status,provider=None,error=None):
        value=str(status or "").upper()
        if value not in ("RESOLVED","PARTIAL","UNRESOLVED","DEFERRED","ERROR"):
            raise ValueError("unsupported mediator status")
        with self._connection() as db:
            cursor=db.execute("""UPDATE watchlist_items SET mediator_status=?,
              mediator_provider=?,mediator_error=?,mediator_checked_at=CURRENT_TIMESTAMP,
              updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (value,str(provider) if provider else None,str(error) if error else None,local_id))
            if cursor.rowcount!=1: raise KeyError("watchlist item not found")
