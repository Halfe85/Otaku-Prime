# -*- coding: utf-8 -*-
"""Bounded application log used by the local administration interface."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager


class AppLogStore:
    def __init__(self, db_path, max_entries=1000):
        self.db_path=db_path; self.max_entries=max(100,int(max_entries))

    @contextmanager
    def _connection(self):
        db=sqlite3.connect(self.db_path,timeout=10); db.row_factory=sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def initialize(self):
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS app_logs(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              level TEXT NOT NULL,source TEXT NOT NULL,message TEXT NOT NULL)""")

    def write(self,level,source,message):
        with self._connection() as db:
            db.execute("INSERT INTO app_logs(level,source,message) VALUES(?,?,?)",
              (str(level).upper()[:16],str(source)[:64],str(message)[:4000]))
            db.execute("""DELETE FROM app_logs WHERE id IN(
              SELECT id FROM app_logs ORDER BY id DESC LIMIT -1 OFFSET ?)""",
              (self.max_entries,))

    def list(self,after_id=0,limit=200):
        limit=max(1,min(500,int(limit)))
        with self._connection() as db:
            return [dict(row) for row in db.execute("""SELECT id,created_at,level,source,message
              FROM app_logs WHERE id>? ORDER BY id LIMIT ?""",(int(after_id),limit))]
