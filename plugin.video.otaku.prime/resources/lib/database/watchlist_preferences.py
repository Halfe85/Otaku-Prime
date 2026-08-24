# -*- coding: utf-8 -*-
"""Small per-user watchlist import preferences."""
import sqlite3
from contextlib import contextmanager

class WatchlistPreferenceStore:
    def __init__(self, db_path): self.db_path = db_path
    @contextmanager
    def _connection(self):
        db=sqlite3.connect(self.db_path,timeout=10); db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()
    def initialize(self):
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS watchlist_preferences(
              user_id INTEGER PRIMARY KEY,mature_content INTEGER NOT NULL DEFAULT 0
              CHECK(mature_content IN(0,1)),updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE)""")
    def mature_content(self,user_id):
        with self._connection() as db:
            row=db.execute("SELECT mature_content FROM watchlist_preferences WHERE user_id=?",
                           (int(user_id),)).fetchone()
            return bool(row[0]) if row else False
    def set_mature_content(self,user_id,enabled):
        with self._connection() as db:
            db.execute("""INSERT INTO watchlist_preferences(user_id,mature_content) VALUES(?,?)
              ON CONFLICT(user_id) DO UPDATE SET mature_content=excluded.mature_content,
              updated_at=CURRENT_TIMESTAMP""",(int(user_id),int(bool(enabled))))
