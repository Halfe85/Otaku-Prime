# -*- coding: utf-8 -*-
"""Persistent physical-library identity for Prime generated media.

Physical folder names are display metadata. Prime ownership is the opaque Prime
ID written into Local Information NFO files and persisted in this registry.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import xml.etree.ElementTree as ElementTree
from contextlib import contextmanager

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
PLUGIN_BASE = "plugin://plugin.video.otaku.prime/play/library/"
MEDIA_SERIES = "series"
MEDIA_MOVIE = "movie"
MEDIA_ROOTS = {
    MEDIA_SERIES: "TV-Series",
    MEDIA_MOVIE: "Movies",
}


class PhysicalLibraryIdentityConflict(RuntimeError):
    """A physical path is already owned by another Prime media identity."""


class PhysicalLibraryIdentityRegistry:
    """Map one Prime media ID to one stable generated directory."""

    def __init__(self, db_path, root_path):
        self.db_path = str(db_path)
        self.root_path = os.path.abspath(str(root_path))

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self):
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS prime_physical_directories(
              media_type TEXT NOT NULL CHECK(media_type IN('series','movie')),
              prime_id TEXT NOT NULL,
              directory TEXT NOT NULL,
              kodi_cleanup_pending INTEGER NOT NULL DEFAULT 0
                CHECK(kodi_cleanup_pending IN(0,1)),
              created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(media_type,prime_id),
              UNIQUE(media_type,directory)
            )""")

    def media_root(self, media_type):
        if media_type not in MEDIA_ROOTS:
            raise ValueError("unsupported Prime physical media type")
        return os.path.join(self.root_path, MEDIA_ROOTS[media_type])

    def _safe_directory(self, media_type, directory):
        root = os.path.abspath(self.media_root(media_type))
        path = os.path.abspath(str(directory or ""))
        try:
            inside = os.path.commonpath((root, path)) == root
        except ValueError:
            inside = False
        if not inside or path == root:
            raise PhysicalLibraryIdentityConflict(
                "refusing Prime physical identity outside generated library root"
            )
        return path

    @staticmethod
    def _xml_prime_id(path, expected_root):
        try:
            root = ElementTree.parse(path).getroot()
        except (OSError, ElementTree.ParseError):
            return None
        if str(root.tag or "").lower() != expected_root:
            return None
        for node in root.findall("uniqueid"):
            if str(node.get("type") or "").strip().lower() == "prime":
                value = str(node.text or "").strip().lower()
                return value or None
        return None

    @staticmethod
    def _prime_id_from_strm(path, media_type):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                value = str(handle.readline() or "").strip()
        except OSError:
            return None
        if not value.startswith(PLUGIN_BASE):
            return None
        local_id = value[len(PLUGIN_BASE):].split("?", 1)[0].strip().lower()
        if media_type == MEDIA_SERIES and len(local_id) >= 18:
            return local_id[:6]
        if media_type == MEDIA_MOVIE and len(local_id) == 6:
            return local_id
        return None

    def directory_prime_id(self, media_type, directory):
        path = self._safe_directory(media_type, directory)
        if not os.path.isdir(path):
            return None
        if media_type == MEDIA_SERIES:
            value = self._xml_prime_id(os.path.join(path, "tvshow.nfo"), "tvshow")
            if value:
                return value
            for current_root, _dirs, files in os.walk(path):
                for name in sorted(files):
                    if not name.lower().endswith(".strm"):
                        continue
                    value = self._prime_id_from_strm(
                        os.path.join(current_root, name), MEDIA_SERIES
                    )
                    if value:
                        return value
            return None

        for name in sorted(os.listdir(path)):
            if not name.lower().endswith(".nfo"):
                continue
            value = self._xml_prime_id(os.path.join(path, name), "movie")
            if value:
                return value
        for name in sorted(os.listdir(path)):
            if not name.lower().endswith(".strm"):
                continue
            value = self._prime_id_from_strm(os.path.join(path, name), MEDIA_MOVIE)
            if value:
                return value
        return None

    def discover(self, media_type, prime_id=None):
        root = self.media_root(media_type)
        wanted = str(prime_id or "").strip().lower() or None
        if not os.path.isdir(root):
            return []
        result = []
        for name in sorted(os.listdir(root)):
            directory = os.path.join(root, name)
            if not os.path.isdir(directory):
                continue
            owner = self.directory_prime_id(media_type, directory)
            if not owner or (wanted is not None and owner != wanted):
                continue
            result.append({
                "media_type": media_type,
                "prime_id": owner,
                "directory": os.path.abspath(directory),
            })
        return result

    def mapped(self, media_type, prime_id):
        with self._connection() as db:
            row = db.execute("""SELECT directory,kodi_cleanup_pending
              FROM prime_physical_directories
              WHERE media_type=? AND prime_id=?""",
                (str(media_type), str(prime_id).lower()),).fetchone()
        return dict(row) if row else None

    def _bind(self, media_type, prime_id, directory, cleanup_pending=False):
        path = self._safe_directory(media_type, directory)
        try:
            with self._connection() as db:
                db.execute("""INSERT INTO prime_physical_directories(
                  media_type,prime_id,directory,kodi_cleanup_pending)
                  VALUES(?,?,?,?)
                  ON CONFLICT(media_type,prime_id) DO UPDATE SET
                    directory=excluded.directory,
                    kodi_cleanup_pending=MAX(
                      prime_physical_directories.kodi_cleanup_pending,
                      excluded.kodi_cleanup_pending),
                    updated_at=CURRENT_TIMESTAMP""",
                    (str(media_type), str(prime_id).lower(), path,
                     int(bool(cleanup_pending))),)
        except sqlite3.IntegrityError as exc:
            raise PhysicalLibraryIdentityConflict(
                "Prime physical directory is already mapped to another media ID: {}".format(path)
            ) from exc
        return path

    def mark_cleanup_complete(self, media_type, prime_id):
        with self._connection() as db:
            db.execute("""UPDATE prime_physical_directories
              SET kodi_cleanup_pending=0,updated_at=CURRENT_TIMESTAMP
              WHERE media_type=? AND prime_id=?""",
                (str(media_type), str(prime_id).lower()))

    def clear(self):
        with self._connection() as db:
            db.execute("DELETE FROM prime_physical_directories")

    @staticmethod
    def _merge_generated_directory(source, destination):
        os.makedirs(destination, exist_ok=True)
        for current_root, dirs, files in os.walk(source):
            relative = os.path.relpath(current_root, source)
            target_root = destination if relative == "." else os.path.join(destination, relative)
            os.makedirs(target_root, exist_ok=True)
            for directory in dirs:
                os.makedirs(os.path.join(target_root, directory), exist_ok=True)
            for name in files:
                src = os.path.join(current_root, name)
                dst = os.path.join(target_root, name)
                if not os.path.exists(dst):
                    os.replace(src, dst)
        shutil.rmtree(source)

    def resolve(self, media_type, prime_id, desired_directory):
        """Return the one canonical directory, migrating/merging stale names."""
        prime_id = str(prime_id or "").strip().lower()
        if not prime_id:
            raise ValueError("Prime media ID is required for physical projection")
        desired = self._safe_directory(media_type, desired_directory)
        mapped = self.mapped(media_type, prime_id)
        discovered = self.discover(media_type, prime_id=prime_id)
        existing = [row["directory"] for row in discovered]
        mapped_path = self._safe_directory(media_type, mapped["directory"]) if mapped else None
        if mapped_path and os.path.isdir(mapped_path) and mapped_path not in existing:
            existing.insert(0, mapped_path)

        cleanup_pending = bool(mapped and mapped.get("kodi_cleanup_pending"))
        migrated_from = []
        duplicates_removed = []

        if mapped_path and os.path.isdir(mapped_path):
            canonical = mapped_path
        elif desired in existing:
            canonical = desired
        elif existing:
            canonical = sorted(existing)[0]
        else:
            canonical = desired

        if os.path.isdir(canonical) and canonical != desired:
            desired_owner = self.directory_prime_id(media_type, desired) if os.path.isdir(desired) else None
            if not os.path.exists(desired):
                os.replace(canonical, desired)
                migrated_from.append(canonical)
                canonical = desired
                cleanup_pending = True
            elif desired_owner == prime_id:
                self._merge_generated_directory(canonical, desired)
                migrated_from.append(canonical)
                canonical = desired
                cleanup_pending = True
            else:
                LOGGER.warning(
                    "Prime kept stable physical directory because desired display path is occupied: "
                    "type=%s prime=%s current=%s desired=%s owner=%s",
                    media_type, prime_id, canonical, desired, desired_owner,
                )

        for duplicate in list(existing):
            duplicate = os.path.abspath(duplicate)
            if duplicate == canonical or not os.path.isdir(duplicate):
                continue
            if self.directory_prime_id(media_type, duplicate) != prime_id:
                continue
            self._merge_generated_directory(duplicate, canonical)
            duplicates_removed.append(duplicate)
            cleanup_pending = True

        self._bind(
            media_type, prime_id, canonical,
            cleanup_pending=cleanup_pending,
        )
        return {
            "media_type": media_type,
            "prime_id": prime_id,
            "directory": canonical,
            "desired_directory": desired,
            "migrated_from": migrated_from,
            "duplicates_removed": duplicates_removed,
            "kodi_cleanup_pending": cleanup_pending,
        }

    @staticmethod
    def _prime_generated_strm(path):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                return str(handle.readline() or "").strip().startswith(PLUGIN_BASE)
        except OSError:
            return False

    def prune_series_files(self, directory, expected_strm_paths):
        """Remove obsolete generated STRM/NFO pairs after a title/coordinate change."""
        root = self._safe_directory(MEDIA_SERIES, directory)
        expected = {os.path.abspath(value) for value in expected_strm_paths or []}
        removed = []
        if not os.path.isdir(root):
            return removed
        for current_root, _dirs, files in os.walk(root):
            for name in files:
                if not name.lower().endswith(".strm"):
                    continue
                path = os.path.abspath(os.path.join(current_root, name))
                if path in expected or not self._prime_generated_strm(path):
                    continue
                try:
                    os.remove(path)
                    removed.append(path)
                except FileNotFoundError:
                    pass
                companion = os.path.splitext(path)[0] + ".nfo"
                try:
                    os.remove(companion)
                    removed.append(companion)
                except FileNotFoundError:
                    pass
        return removed

    def prune_movie_files(self, prime_id, directory, expected_strm, expected_nfo):
        root = self._safe_directory(MEDIA_MOVIE, directory)
        if not os.path.isdir(root):
            return []
        expected_strm = os.path.abspath(expected_strm)
        expected_nfo = os.path.abspath(expected_nfo)
        removed = []
        for name in os.listdir(root):
            path = os.path.abspath(os.path.join(root, name))
            if path == expected_strm or path == expected_nfo or not os.path.isfile(path):
                continue
            if name.lower().endswith(".strm") and self._prime_generated_strm(path):
                try:
                    os.remove(path)
                    removed.append(path)
                except FileNotFoundError:
                    pass
                continue
            if name.lower().endswith(".nfo") and self._xml_prime_id(path, "movie") == str(prime_id).lower():
                try:
                    os.remove(path)
                    removed.append(path)
                except FileNotFoundError:
                    pass
        return removed
