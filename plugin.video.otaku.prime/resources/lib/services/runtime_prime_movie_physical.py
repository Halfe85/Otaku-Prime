# -*- coding: utf-8 -*-
"""Kodi runtime settings for Prime's folder-per-movie physical layout."""
from __future__ import annotations

import sqlite3

from resources.lib.logging_config import get_logger
from resources.lib.services.prime_movie_physical import PrimeMoviePhysicalSupport


LOGGER = get_logger(__name__)


class RuntimePrimeMoviePhysicalSupport(PrimeMoviePhysicalSupport):
    """Enable Kodi's folder-name + recursive movie scanning for Prime Movies."""

    def ensure_local_content(self):
        result = super().ensure_local_content()
        if not result.get("configured"):
            return result
        try:
            with sqlite3.connect(self.video_database_path, timeout=3) as db:
                current = db.execute(
                    "SELECT scanRecursive,useFolderNames FROM path WHERE strPath=?",
                    (self.source_url,),
                ).fetchone()
                changed = current != (1, 1)
                if changed:
                    db.execute(
                        "UPDATE path SET scanRecursive=1,useFolderNames=1,strHash='',"
                        "updated_at=CURRENT_TIMESTAMP WHERE strPath=?"
                        if self._path_table_has_updated_at(db)
                        else "UPDATE path SET scanRecursive=1,useFolderNames=1,strHash='' WHERE strPath=?",
                        (self.source_url,),
                    )
            result = dict(result)
            result["changed"] = bool(result.get("changed") or changed)
            result["scan_recursive"] = 1
            result["use_folder_names"] = 1
            self._content_result = dict(result)
            LOGGER.info(
                "Kodi Movies folder scanning configured: path=%s recursive=1 useFolderNames=1 changed=%s",
                self.source_url, changed,
            )
            return result
        except (OSError, sqlite3.Error) as exc:
            result = dict(result)
            result.update({"configured": False, "error": str(exc)})
            self._content_result = dict(result)
            LOGGER.exception(
                "Prime Physical could not enable folder scanning for Movies source %s",
                self.source_url,
            )
            return result

    @staticmethod
    def _path_table_has_updated_at(db):
        return "updated_at" in {
            row[1] for row in db.execute("PRAGMA table_info(path)")
        }
