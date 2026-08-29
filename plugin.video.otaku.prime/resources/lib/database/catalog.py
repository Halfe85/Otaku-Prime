# -*- coding: utf-8 -*-
"""Prime catalogue: franchise -> watchlist season -> episode."""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import contextmanager

from resources.lib.logging_config import get_logger
from resources.lib.services.remote_identity import best_title_similarity,clean_remote_text


HEX_SEGMENT_LENGTH=6
LOGGER=get_logger(__name__)


class CatalogStore:
    """Store opaque hierarchical catalogue identities and mediated library metadata."""
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
            legacy_cast=bool(db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_cast'"
            ).fetchone())
            if legacy_cast:
                # Alpha schema reset: the flattened cast rows are deliberately
                # discarded. They cannot preserve staff/character identities or
                # distinguish series, season, and episode credits.
                db.executescript("""
                DROP TABLE IF EXISTS character_media_links;
                DROP TABLE IF EXISTS staff_character_links;
                DROP TABLE IF EXISTS characters;
                DROP TABLE IF EXISTS staff;
                DROP TABLE series_cast;
                """)
            db.executescript("""
            CREATE TABLE IF NOT EXISTS tv_series(
              local_id TEXT PRIMARY KEY
                CHECK(length(local_id)=6 AND local_id NOT GLOB '*[^0-9a-f]*'),
              english_name TEXT,
              romaji_name TEXT,
              root_simkl_id TEXT UNIQUE,
              root_anilist_id TEXT UNIQUE,
              tvdb_id TEXT UNIQUE,
              source_provider TEXT,
              source_media_format TEXT,
              publish_year INTEGER,
              overview TEXT,
              runtime_minutes INTEGER,
              air_status TEXT,
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
              release_status TEXT,
              placement_state TEXT NOT NULL DEFAULT 'COMPLETE'
                CHECK(placement_state IN('STRUCTURE_ONLY','COMPLETE')),
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
              title TEXT,
              overview TEXT,
              runtime_minutes INTEGER,
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

            CREATE TABLE IF NOT EXISTS staff(
              local_id TEXT PRIMARY KEY
                CHECK(length(local_id)=6 AND local_id NOT GLOB '*[^0-9a-f]*'),
              anilist_id TEXT UNIQUE,
              name TEXT NOT NULL,
              trivia TEXT,
              date_of_birth TEXT,
              date_of_death TEXT,
              age INTEGER CHECK(age IS NULL OR age>=0),
              image_url TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_staff_name ON staff(name COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS characters(
              local_id TEXT PRIMARY KEY
                CHECK(length(local_id)=6 AND local_id NOT GLOB '*[^0-9a-f]*'),
              anilist_id TEXT UNIQUE,
              name TEXT NOT NULL,
              trivia TEXT,
              image_url TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS ix_characters_name ON characters(name COLLATE NOCASE);

            CREATE TABLE IF NOT EXISTS staff_character_links(
              staff_local_id TEXT NOT NULL,
              character_local_id TEXT NOT NULL,
              credit_type TEXT NOT NULL DEFAULT 'voice_actor',
              language TEXT NOT NULL DEFAULT '',
              source_provider TEXT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(staff_local_id,character_local_id,credit_type,language),
              FOREIGN KEY(staff_local_id) REFERENCES staff(local_id) ON DELETE CASCADE,
              FOREIGN KEY(character_local_id) REFERENCES characters(local_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS ix_staff_character_character
              ON staff_character_links(character_local_id,staff_local_id);

            CREATE TABLE IF NOT EXISTS character_media_links(
              local_id INTEGER PRIMARY KEY AUTOINCREMENT,
              character_local_id TEXT NOT NULL,
              related_series_id TEXT,
              related_season_id TEXT,
              related_episode_id TEXT,
              source_provider TEXT,
              sort_order INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              CHECK((related_series_id IS NOT NULL)+(related_season_id IS NOT NULL)+
                    (related_episode_id IS NOT NULL)=1),
              FOREIGN KEY(character_local_id) REFERENCES characters(local_id) ON DELETE CASCADE,
              FOREIGN KEY(related_series_id) REFERENCES tv_series(local_id) ON DELETE CASCADE,
              FOREIGN KEY(related_season_id) REFERENCES seasons(local_id) ON DELETE CASCADE,
              FOREIGN KEY(related_episode_id) REFERENCES episodes(local_id) ON DELETE CASCADE
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_character_media_series_credit
              ON character_media_links(character_local_id,related_series_id)
              WHERE related_series_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS ux_character_media_season_credit
              ON character_media_links(character_local_id,related_season_id)
              WHERE related_season_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS ux_character_media_episode_credit
              ON character_media_links(character_local_id,related_episode_id)
              WHERE related_episode_id IS NOT NULL;
            """)
            self._add_columns(db,"tv_series",(
                ("tvdb_id","TEXT"),("root_anilist_id","TEXT"),
                ("source_provider","TEXT"),("source_media_format","TEXT"),
                ("publish_year","INTEGER"),("overview","TEXT"),
                ("runtime_minutes","INTEGER"),("air_status","TEXT")))
            self._add_columns(db,"seasons",(
                ("provider_path","TEXT"),("placement_source","TEXT"),
                ("first_episode","INTEGER"),("last_episode","INTEGER"),
                ("release_status","TEXT"),
                ("placement_state","TEXT NOT NULL DEFAULT 'COMPLETE'")))
            self._add_columns(db,"episodes",(
                ("source_episode_number","INTEGER NOT NULL DEFAULT 1"),
                ("title","TEXT"),("overview","TEXT"),("runtime_minutes","INTEGER")))
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_tv_series_tvdb
              ON tv_series(tvdb_id) WHERE tvdb_id IS NOT NULL""")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS ux_tv_series_anilist
              ON tv_series(root_anilist_id) WHERE root_anilist_id IS NOT NULL""")
            if legacy_cast and db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_items'"
            ).fetchone():
                db.execute("""UPDATE watchlist_items SET added_to_library=0,
                  mediator_ready=1,mediator_status='PARTIAL',
                  mediator_error='Staff and character metadata rebuild required',
                  updated_at=CURRENT_TIMESTAMP""")
                LOGGER.warning(
                    "Discarded legacy series_cast data; queued Prime library for staff/character rebuild")
            self._repair_encoded_text(db)

    @staticmethod
    def _repair_encoded_text(db):
        targets={
            "tv_series":("english_name","romaji_name","overview"),
            "seasons":("english_name","romaji_name"),
            "episodes":("title","overview"),
            "staff":("name","trivia"),
            "characters":("name","trivia"),
        }
        for table,columns in targets.items():
            selection=",".join(columns)
            for row in db.execute(
                "SELECT rowid AS source_rowid,{} FROM {}".format(selection,table)
            ).fetchall():
                values=[clean_remote_text(row[name]) for name in columns]
                if any(values[index]!=row[name] for index,name in enumerate(columns)):
                    assignments=",".join("{}=?".format(name) for name in columns)
                    db.execute(
                        "UPDATE {} SET {},updated_at=CURRENT_TIMESTAMP WHERE rowid=?".format(
                            table,assignments),tuple(values)+(row["source_rowid"],))

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

    @staticmethod
    def _series_name_match(db,english_name=None,romaji_name=None):
        expected=[value for value in (english_name,romaji_name) if value]
        if not expected: return None
        scored=[]
        for row in db.execute("SELECT * FROM tv_series").fetchall():
            actual=[value for value in (row["english_name"],row["romaji_name"]) if value]
            similarity=best_title_similarity(expected,actual)
            if similarity>=0.88:
                scored.append((similarity,row))
        scored.sort(key=lambda value:value[0],reverse=True)
        if not scored: return None
        if len(scored)>1 and scored[0][0]-scored[1][0]<0.08:
            return None
        return scored[0][1]

    @staticmethod
    def _assert_remote_id_available(db,column,value,local_id):
        if value in (None,""): return
        collision=db.execute(
            "SELECT local_id FROM tv_series WHERE {}=? AND local_id<>?".format(column),
            (str(value),str(local_id)),).fetchone()
        if collision:
            raise ValueError(
                "{} {} already belongs to Prime series {}".format(
                    column,str(value),collision["local_id"]))

    @staticmethod
    def _resolve_split_identity(db,identity_rows,english_name=None,romaji_name=None):
        """Select and repair a unique 2-of-3 remote-identity majority.

        Historical mediation could attach one stale remote ID to a different
        Prime series. Never merge local series or their seasons here: only
        detach the contradicted remote mapping when two other validated IDs
        and the incoming title independently select one row.
        """
        by_local_id={}
        for column,value,row in identity_rows:
            if row:
                by_local_id.setdefault(row["local_id"],[]).append((column,value,row))
        if len(by_local_id)<=1:
            return next(iter(by_local_id.values()))[0][2] if by_local_id else None
        ranked=sorted(by_local_id.items(),key=lambda pair:len(pair[1]),reverse=True)
        winner_id,winner_matches=ranked[0]
        runner_up_count=len(ranked[1][1])
        winner=winner_matches[0][2]
        incoming_titles=[value for value in (english_name,romaji_name) if value]
        stored_titles=[value for value in (winner["english_name"],winner["romaji_name"]) if value]
        title_similarity=best_title_similarity(incoming_titles,stored_titles)
        if len(winner_matches)<2 or len(winner_matches)==runner_up_count or title_similarity<0.88:
            raise ValueError(
                "validated remote identities point at different Prime series: {}".format(
                    ", ".join(sorted(by_local_id))))
        for column,value,row in identity_rows:
            if not row or row["local_id"]==winner_id:
                continue
            db.execute(
                "UPDATE tv_series SET {}=NULL,updated_at=CURRENT_TIMESTAMP "
                "WHERE local_id=? AND {}=?".format(column,column),
                (row["local_id"],str(value)))
            LOGGER.warning(
                "Reassigned stale catalogue identity %s=%s from Prime series %s to %s",
                column,value,row["local_id"],winner_id)
        return winner

    def get_or_create_series(self,english_name=None,romaji_name=None,root_simkl_id=None,
                             tvdb_id=None,root_anilist_id=None,source_provider=None,
                             source_media_format=None,publish_year=None,overview=None,
                             runtime_minutes=None,air_status=None):
        """Resolve a Prime series while treating remote IDs as replaceable mappings."""
        root=str(root_simkl_id) if root_simkl_id not in (None,"") else None
        tvdb=str(tvdb_id) if tvdb_id not in (None,"") else None
        anilist=str(root_anilist_id) if root_anilist_id not in (None,"") else None
        english_name=clean_remote_text(english_name)
        romaji_name=clean_remote_text(romaji_name)
        overview=clean_remote_text(overview)
        year=int(publish_year) if publish_year not in (None,"") else None
        runtime=int(runtime_minutes) if runtime_minutes not in (None,"") else None
        with self._connection() as db:
            root_row=db.execute("SELECT * FROM tv_series WHERE root_simkl_id=?",(root,)).fetchone() if root else None
            tvdb_row=db.execute("SELECT * FROM tv_series WHERE tvdb_id=?",(tvdb,)).fetchone() if tvdb else None
            anilist_row=db.execute(
                "SELECT * FROM tv_series WHERE root_anilist_id=?",(anilist,)
            ).fetchone() if anilist else None
            identity_rows=[("root_simkl_id",root,root_row),
                           ("tvdb_id",tvdb,tvdb_row),
                           ("root_anilist_id",anilist,anilist_row)]
            row=self._resolve_split_identity(
                db,identity_rows,english_name=english_name,romaji_name=romaji_name)
            if not row:
                row=self._series_name_match(db,english_name,romaji_name)
            if row:
                self._assert_remote_id_available(db,"root_simkl_id",root,row["local_id"])
                self._assert_remote_id_available(db,"tvdb_id",tvdb,row["local_id"])
                self._assert_remote_id_available(db,"root_anilist_id",anilist,row["local_id"])
                db.execute("""UPDATE tv_series SET
                  english_name=COALESCE(?,english_name),romaji_name=COALESCE(?,romaji_name),
                  root_simkl_id=COALESCE(?,root_simkl_id),tvdb_id=COALESCE(?,tvdb_id),
                  root_anilist_id=COALESCE(?,root_anilist_id),
                  source_provider=COALESCE(source_provider,?),
                  source_media_format=COALESCE(source_media_format,?),
                  publish_year=COALESCE(?,publish_year),overview=COALESCE(?,overview),
                  runtime_minutes=COALESCE(?,runtime_minutes),air_status=COALESCE(?,air_status),
                  updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (english_name,romaji_name,root,tvdb,anilist,source_provider,
                   source_media_format,year,overview,runtime,air_status,row["local_id"]))
                return dict(db.execute("SELECT * FROM tv_series WHERE local_id=?",(row["local_id"],)).fetchone())
            local_id=self._new_local_id(db,"tv_series")
            db.execute("""INSERT INTO tv_series(local_id,english_name,romaji_name,
              root_simkl_id,root_anilist_id,tvdb_id,source_provider,source_media_format,
              publish_year,overview,runtime_minutes,air_status)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(local_id,english_name,romaji_name,root,anilist,
              tvdb,source_provider,source_media_format,year,overview,runtime,air_status))
            return dict(db.execute("SELECT * FROM tv_series WHERE local_id=?",(local_id,)).fetchone())

    @staticmethod
    def _credit_entity(entry,key,legacy_name):
        value=(entry or {}).get(key) or {}
        if not isinstance(value,dict): value={"name":value}
        result=dict(value)
        if not result.get("name"):
            result["name"]=(entry or {}).get(legacy_name)
        return result

    def _upsert_staff(self,db,value):
        name=str(clean_remote_text((value or {}).get("name")) or "").strip()
        if not name: return None
        anilist_id=(value or {}).get("anilist_id")
        anilist_id=str(anilist_id) if anilist_id not in (None,"") else None
        row=(db.execute("SELECT * FROM staff WHERE anilist_id=?",(anilist_id,)).fetchone()
             if anilist_id else db.execute(
                 "SELECT * FROM staff WHERE name=? COLLATE NOCASE ORDER BY local_id LIMIT 1",
                 (name,)).fetchone())
        trivia=clean_remote_text((value or {}).get("trivia"))
        dob=(value or {}).get("date_of_birth")
        dod=(value or {}).get("date_of_death")
        try: age=int((value or {}).get("age")) if (value or {}).get("age") not in (None,"") else None
        except (TypeError,ValueError): age=None
        image=(value or {}).get("image_url")
        if row:
            db.execute("""UPDATE staff SET anilist_id=COALESCE(?,anilist_id),name=?,
              trivia=COALESCE(?,trivia),date_of_birth=COALESCE(?,date_of_birth),
              date_of_death=COALESCE(?,date_of_death),age=COALESCE(?,age),
              image_url=COALESCE(?,image_url),updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (anilist_id,name,trivia,dob,dod,age,image,row["local_id"]))
            return row["local_id"]
        local_id=self._new_local_id(db,"staff")
        db.execute("""INSERT INTO staff(local_id,anilist_id,name,trivia,date_of_birth,
          date_of_death,age,image_url) VALUES(?,?,?,?,?,?,?,?)""",
          (local_id,anilist_id,name,trivia,dob,dod,age,image))
        return local_id

    def _upsert_character(self,db,value):
        name=str(clean_remote_text((value or {}).get("name")) or "").strip()
        if not name: return None
        anilist_id=(value or {}).get("anilist_id")
        anilist_id=str(anilist_id) if anilist_id not in (None,"") else None
        row=(db.execute("SELECT * FROM characters WHERE anilist_id=?",(anilist_id,)).fetchone()
             if anilist_id else db.execute(
                 "SELECT * FROM characters WHERE name=? COLLATE NOCASE ORDER BY local_id LIMIT 1",
                 (name,)).fetchone())
        trivia=clean_remote_text((value or {}).get("trivia")); image=(value or {}).get("image_url")
        if row:
            db.execute("""UPDATE characters SET anilist_id=COALESCE(?,anilist_id),name=?,
              trivia=COALESCE(?,trivia),image_url=COALESCE(?,image_url),
              updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
              (anilist_id,name,trivia,image,row["local_id"]))
            return row["local_id"]
        local_id=self._new_local_id(db,"characters")
        db.execute("""INSERT INTO characters(local_id,anilist_id,name,trivia,image_url)
          VALUES(?,?,?,?,?)""",(local_id,anilist_id,name,trivia,image))
        return local_id

    @staticmethod
    def _media_scope(series_id=None,season_id=None,episode_id=None):
        values={"related_series_id":series_id,"related_season_id":season_id,
                "related_episode_id":episode_id}
        selected=[(column,str(value)) for column,value in values.items()
                  if value not in (None,"")]
        if len(selected)!=1:
            raise ValueError("exactly one series, season, or episode credit scope is required")
        return selected[0]

    def replace_media_credits(self,credits,series_id=None,season_id=None,episode_id=None,
                              source_provider=None):
        """Store staff, characters, their relationship, and one exact media scope.

        Missing or empty provider data is non-authoritative and therefore does
        not erase credits already learned from another season/provider.
        """
        if not credits: return 0
        column,media_id=self._media_scope(series_id,season_id,episode_id)
        table={"related_series_id":"tv_series","related_season_id":"seasons",
               "related_episode_id":"episodes"}[column]
        with self._connection() as db:
            if not db.execute("SELECT 1 FROM {} WHERE local_id=?".format(table),(media_id,)).fetchone():
                raise KeyError("credit media scope was not found")
            db.execute("DELETE FROM character_media_links WHERE {}=?".format(column),(media_id,))
            inserted=0
            for index,entry in enumerate(credits):
                staff_value=self._credit_entity(entry,"person","person_name")
                character_value=self._credit_entity(entry,"character","character_name")
                character_id=self._upsert_character(db,character_value)
                if not character_id: continue
                staff_id=self._upsert_staff(db,staff_value)
                credit_type=str((entry or {}).get("credit_type") or "voice_actor")
                language=str((entry or {}).get("language") or "")
                provider=str((entry or {}).get("source_provider") or source_provider or "") or None
                sort_order=int((entry or {}).get("sort_order",index))
                if staff_id:
                    db.execute("""INSERT INTO staff_character_links(staff_local_id,
                      character_local_id,credit_type,language,source_provider)
                      VALUES(?,?,?,?,?) ON CONFLICT(staff_local_id,character_local_id,
                      credit_type,language) DO UPDATE SET source_provider=excluded.source_provider,
                      updated_at=CURRENT_TIMESTAMP""",
                      (staff_id,character_id,credit_type,language,provider))
                values={"related_series_id":None,"related_season_id":None,
                        "related_episode_id":None}; values[column]=media_id
                db.execute("""INSERT OR IGNORE INTO character_media_links(
                  character_local_id,related_series_id,related_season_id,related_episode_id,
                  source_provider,sort_order) VALUES(?,?,?,?,?,?)""",
                  (character_id,values["related_series_id"],values["related_season_id"],
                   values["related_episode_id"],provider,sort_order))
                inserted+=1
            return inserted

    @staticmethod
    def _credits_for_media(db,column,media_id):
        rows=db.execute("""SELECT staff_link.credit_type,staff_link.language,
          COALESCE(staff_link.source_provider,link.source_provider) AS source_provider,
          link.sort_order,staff.local_id AS staff_local_id,staff.anilist_id AS staff_anilist_id,
          staff.name AS staff_name,staff.trivia AS staff_trivia,
          staff.date_of_birth,staff.date_of_death,staff.age,staff.image_url AS staff_image_url,
          characters.local_id AS character_local_id,
          characters.anilist_id AS character_anilist_id,characters.name AS character_name,
          characters.trivia AS character_trivia,characters.image_url AS character_image_url
          FROM character_media_links link
          JOIN characters ON characters.local_id=link.character_local_id
          LEFT JOIN staff_character_links staff_link
            ON staff_link.character_local_id=characters.local_id
          LEFT JOIN staff ON staff.local_id=staff_link.staff_local_id
          WHERE link.{}=? ORDER BY link.sort_order,characters.name,staff.name""".format(column),
          (str(media_id),)).fetchall()
        result=[]
        for row in rows:
            result.append({
                "person_name":row["staff_name"],"character_name":row["character_name"],
                "credit_type":row["credit_type"] or "voice_actor",
                "language":row["language"] or "",
                "source_provider":row["source_provider"],"sort_order":row["sort_order"],
                "person":({"local_id":row["staff_local_id"],
                           "anilist_id":row["staff_anilist_id"],"name":row["staff_name"],
                           "trivia":row["staff_trivia"],"date_of_birth":row["date_of_birth"],
                           "date_of_death":row["date_of_death"],"age":row["age"],
                           "image_url":row["staff_image_url"]}
                          if row["staff_local_id"] else {}),
                "character":{"local_id":row["character_local_id"],
                             "anilist_id":row["character_anilist_id"],
                             "name":row["character_name"],"trivia":row["character_trivia"],
                             "image_url":row["character_image_url"]},
            })
        return result

    @staticmethod
    def _people_for_series(series,cast,seasons):
        """Build UI-ready staff and character views for one franchise.

        Credits remain scoped to their exact series, season, or episode in the
        database.  This projection only gathers those scopes for display and
        keeps the normalized entity links intact.
        """
        scoped=[]
        scoped.append((cast,{
            "scope":"series","local_id":series["local_id"],
            "season_number":None,"episode_number":None,
            "title":series.get("english_name") or series.get("romaji_name"),
        }))
        for season in seasons:
            season_number=season.get("season_number")
            scoped.append((season.get("cast") or [],{
                "scope":"season","local_id":season["local_id"],
                "season_number":season_number,"episode_number":None,
                "title":season.get("english_name") or season.get("romaji_name"),
            }))
            for episode in season.get("episodes") or []:
                scoped.append((episode.get("cast") or [],{
                    "scope":"episode","local_id":episode["local_id"],
                    "season_number":season_number,
                    "episode_number":episode.get("episode_number"),
                    "title":episode.get("title"),
                }))

        characters={}
        staff={}
        character_staff_seen=set()
        staff_character_seen=set()
        media_seen=set()
        for credits,media in scoped:
            for credit in credits:
                character=dict(credit.get("character") or {})
                character_id=character.get("local_id")
                if not character_id:
                    continue
                character_view=characters.setdefault(character_id,dict(
                    character,staff=[],media_links=[]))
                media_key=(character_id,media["scope"],media["local_id"])
                if media_key not in media_seen:
                    character_view["media_links"].append(dict(media))
                    media_seen.add(media_key)

                person=dict(credit.get("person") or {})
                staff_id=person.get("local_id")
                if not staff_id:
                    continue
                relationship={
                    "credit_type":credit.get("credit_type") or "voice_actor",
                    "language":credit.get("language") or "",
                    "source_provider":credit.get("source_provider"),
                }
                pair=(staff_id,character_id,relationship["credit_type"],
                      relationship["language"])
                if pair not in character_staff_seen:
                    character_view["staff"].append(dict(person,**relationship))
                    character_staff_seen.add(pair)
                staff_view=staff.setdefault(staff_id,dict(person,characters=[]))
                if pair not in staff_character_seen:
                    staff_view["characters"].append(dict(
                        character,media_links=character_view["media_links"],**relationship))
                    staff_character_seen.add(pair)

        character_rows=sorted(characters.values(),key=lambda value:
                              str(value.get("name") or "").casefold())
        staff_rows=sorted(staff.values(),key=lambda value:
                         str(value.get("name") or "").casefold())
        return staff_rows,character_rows

    def add_watchlist_season(self,series_id,watchlist_item,season_number=None,
                             provider_path=None,placement_source=None,
                             first_episode=None,last_episode=None,
                             english_name=None,romaji_name=None,release_date=None,
                             release_status=None,placement_state="COMPLETE"):
        watchlist_id=str(watchlist_item["local_id"])
        english_name=english_name or watchlist_item.get("english_name")
        romaji_name=romaji_name or watchlist_item.get("romaji_name")
        release_date=release_date or watchlist_item.get("release_date")
        placement_state=str(placement_state or "COMPLETE").upper()
        if placement_state not in ("STRUCTURE_ONLY","COMPLETE"):
            raise ValueError("unsupported season placement state")
        with self._connection() as db:
            row=db.execute("SELECT * FROM seasons WHERE watchlist_local_id=?",(watchlist_id,)).fetchone()
            if row:
                if str(row["related_series_id"])!=str(series_id):
                    raise ValueError(
                        "existing Prime season cannot move from series {} to {}".format(
                            row["related_series_id"],series_id))
                db.execute("""UPDATE seasons SET
                  anilist_id=COALESCE(?,anilist_id),mal_id=COALESCE(?,mal_id),
                  kitsu_id=COALESCE(?,kitsu_id),simkl_id=COALESCE(?,simkl_id),
                  season_number=?,provider_path=?,placement_source=?,first_episode=?,last_episode=?,
                  english_name=COALESCE(?,english_name),romaji_name=COALESCE(?,romaji_name),
                  media_format=COALESCE(?,media_format),release_date=COALESCE(?,release_date),
                  release_status=COALESCE(?,release_status),placement_state=?,
                  updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (watchlist_item.get("anilist_id"),watchlist_item.get("mal_id"),
                   watchlist_item.get("kitsu_id"),watchlist_item.get("simkl_id"),season_number,
                   provider_path,placement_source,first_episode,last_episode,
                   english_name,romaji_name,
                   watchlist_item.get("media_format"),release_date,release_status,
                   placement_state,row["local_id"]))
                return dict(db.execute("SELECT * FROM seasons WHERE local_id=?",(row["local_id"],)).fetchone())
            if not db.execute("SELECT 1 FROM tv_series WHERE local_id=?",(series_id,)).fetchone():
                raise KeyError("TV series not found")
            local_id=self._new_local_id(db,"seasons",str(series_id))
            db.execute("""INSERT INTO seasons(local_id,related_series_id,watchlist_local_id,
              anilist_id,mal_id,kitsu_id,simkl_id,season_number,english_name,romaji_name,
              media_format,release_date,release_status,placement_state,
              provider_path,placement_source,first_episode,last_episode)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (local_id,series_id,watchlist_id,watchlist_item.get("anilist_id"),
               watchlist_item.get("mal_id"),watchlist_item.get("kitsu_id"),
               watchlist_item.get("simkl_id"),season_number,english_name,
               romaji_name,watchlist_item.get("media_format"),
               release_date,release_status,placement_state,provider_path,placement_source,
               first_episode,last_episode))
            return dict(db.execute("SELECT * FROM seasons WHERE local_id=?",(local_id,)).fetchone())

    def add_episode(self,season_id,episode_number,source_episode_number=None,mal_id=None,simkl_id=None,
                    watch_status=False,release_date=None,title=None,overview=None,runtime_minutes=None):
        number=int(episode_number)
        title=clean_remote_text(title)
        overview=clean_remote_text(overview)
        incoming_mal=str(mal_id) if mal_id not in (None,"") else None
        incoming_simkl=str(simkl_id) if simkl_id not in (None,"") else None
        runtime=int(runtime_minutes) if runtime_minutes not in (None,"") else None
        with self._connection() as db:
            row=db.execute("SELECT * FROM episodes WHERE related_season_id=? AND episode_number=?",
                           (season_id,number)).fetchone()
            if row:
                db.execute("""UPDATE episodes SET source_episode_number=?,mal_id=COALESCE(?,mal_id),
                  simkl_id=COALESCE(?,simkl_id),title=COALESCE(?,title),overview=COALESCE(?,overview),
                  runtime_minutes=COALESCE(?,runtime_minutes),release_date=COALESCE(?,release_date),
                  updated_at=CURRENT_TIMESTAMP WHERE local_id=?""",
                  (int(source_episode_number or number),incoming_mal,incoming_simkl,title,overview,
                   runtime,release_date,row["local_id"]))
                return dict(db.execute("SELECT * FROM episodes WHERE local_id=?",(row["local_id"],)).fetchone())
            if not db.execute("SELECT 1 FROM seasons WHERE local_id=?",(season_id,)).fetchone():
                raise KeyError("season not found")
            local_id=self._new_local_id(db,"episodes",str(season_id))
            db.execute("""INSERT INTO episodes(local_id,related_season_id,episode_number,mal_id,
              simkl_id,title,overview,runtime_minutes,watch_status,release_date,source_episode_number)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (local_id,season_id,number,incoming_mal,incoming_simkl,title,overview,runtime,
               int(bool(watch_status)),release_date,int(source_episode_number or number)))
            return dict(db.execute("SELECT * FROM episodes WHERE local_id=?",(local_id,)).fetchone())

    @staticmethod
    def _watchlist_release_fields(db):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_items'").fetchone():
            return set()
        return {row[1] for row in db.execute("PRAGMA table_info(watchlist_items)")}

    def library_series(self):
        """Return lightweight series tiles as the mediator populates the catalogue."""
        with self._connection() as db:
            release_fields=self._watchlist_release_fields(db)
            series_rows=[dict(row) for row in db.execute("SELECT * FROM tv_series ORDER BY english_name,romaji_name,local_id")]
            result=[]
            for series in series_rows:
                seasons=db.execute("SELECT * FROM seasons WHERE related_series_id=?",(series["local_id"],)).fetchall()
                episode_count=sum(int(db.execute(
                    "SELECT COUNT(*) FROM episodes WHERE related_season_id=?",(season["local_id"],)
                ).fetchone()[0]) for season in seasons)
                next_candidates=[]
                if "next_episode_release_epoch" in release_fields:
                    for season in seasons:
                        row=db.execute("""SELECT next_episode_number,next_episode_release_date,
                          next_episode_release_epoch FROM watchlist_items WHERE local_id=?""",
                          (season["watchlist_local_id"],)).fetchone()
                        if row and int(row["next_episode_release_epoch"] or 0)>0:
                            next_candidates.append(dict(row))
                next_item=min(next_candidates,key=lambda row:int(row["next_episode_release_epoch"])) if next_candidates else None
                year=series.get("publish_year")
                if year is None:
                    dates=[season["release_date"] for season in seasons if season["release_date"]]
                    years=[int(str(value)[:4]) for value in dates if str(value)[:4].isdigit()]
                    year=min(years) if years else None
                item=dict(series)
                item.update({
                    "title":series.get("english_name") or series.get("romaji_name") or "Untitled series",
                    "publish_year":year,
                    "season_count":len(seasons),
                    "episode_count":episode_count,
                    "next_episode_number":next_item.get("next_episode_number") if next_item else None,
                    "next_episode_release_date":next_item.get("next_episode_release_date") if next_item else None,
                    "library_status":"RUNNING" if next_item else (series.get("air_status") or "UNKNOWN"),
                })
                result.append(item)
            return result

    def library_series_detail(self,series_id):
        """Return one series with normalized staff/characters and scoped credits."""
        with self._connection() as db:
            row=db.execute("SELECT * FROM tv_series WHERE local_id=?",(str(series_id),)).fetchone()
            if not row:
                return None
            series=dict(row)
            release_fields=self._watchlist_release_fields(db)
            cast=self._credits_for_media(db,"related_series_id",series["local_id"])
            seasons=[]
            for season_row in db.execute("""SELECT * FROM seasons WHERE related_series_id=?
              ORDER BY season_number,local_id""",(series["local_id"],)):
                season=dict(season_row)
                if "next_episode_release_epoch" in release_fields:
                    release=db.execute("""SELECT season_release_date,next_episode_number,
                      next_source_episode_number,next_episode_release_date FROM watchlist_items
                      WHERE local_id=?""",(season["watchlist_local_id"],)).fetchone()
                    if release:
                        season.update(dict(release))
                season["cast"]=self._credits_for_media(
                    db,"related_season_id",season["local_id"])
                episodes=[]
                for episode_row in db.execute("""SELECT * FROM episodes
                  WHERE related_season_id=? ORDER BY episode_number,local_id""",
                  (season["local_id"],)):
                    episode=dict(episode_row)
                    episode["cast"]=self._credits_for_media(
                        db,"related_episode_id",episode["local_id"])
                    episodes.append(episode)
                season["episodes"]=episodes
                seasons.append(season)
            tiles=self.library_series()
            tile=next((value for value in tiles if value["local_id"]==series["local_id"]),None)
            if tile:
                series.update(tile)
            series["cast"]=cast
            series["seasons"]=seasons
            series["staff"],series["characters"]=self._people_for_series(
                series,cast,seasons)
            return series

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
