# -*- coding: utf-8 -*-
"""Kodi Local Information projection for Prime standalone movies."""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import xml.etree.ElementTree as ElementTree

from resources.lib.logging_config import get_logger
from resources.lib.services.prime_physical import (
    LOCAL_INFORMATION_SCRAPER,
    safe_library_name,
)
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)
MOVIE_SOURCE_NAME = "Otaku Prime Movies"
MOVIE_CONTENT = "movies"
PLUGIN_BASE = "plugin://plugin.video.otaku.prime/play/library/"
MOVIE_ART_TYPES = ("poster", "banner", "clearlogo", "clearart", "landscape")
PROVIDER_IDS = ("anilist", "mal", "kitsu", "simkl")


def _clean(value):
    text = str(value or "").strip()
    return text or None


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _date_only(value):
    text = _clean(value)
    return text[:10] if text else None


def _decode_terms(value):
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        decoded = []
    return [str(item).strip() for item in decoded if str(item or "").strip()] \
        if isinstance(decoded, list) else []


def _add_text(parent, tag, value):
    text = _clean(value)
    if text is None:
        return None
    node = ElementTree.SubElement(parent, tag)
    node.text = text
    return node


def _add_unique_id(parent, provider, value, default=False):
    text = _clean(value)
    if text is None:
        return None
    node = ElementTree.SubElement(parent, "uniqueid")
    node.set("type", str(provider))
    if default:
        node.set("default", "true")
    node.text = text
    return node


def _write_if_changed(path, payload):
    encoded = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    try:
        with open(path, "rb") as handle:
            if handle.read() == encoded:
                return False
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=os.path.dirname(path), prefix=".prime-movie-", suffix=".tmp",
        delete=False,
    )
    temporary = handle.name
    try:
        with handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return True


def _xml_bytes(root):
    tree = ElementTree.ElementTree(root)
    try:
        ElementTree.indent(tree, space="    ")
    except AttributeError:
        pass
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


class PrimeMoviePhysicalSupport:
    """Own Prime's separate Kodi Movies source and physical movie files."""

    def __init__(self, physical, artwork_store=None):
        self.physical = physical
        self.catalog_store = physical.catalog_store
        self.root_path = physical.root_path
        self.sources_path = physical.sources_path
        self.video_database_path = physical.video_database_path
        self.source_url = str(os.path.join(self.root_path, "Movies") + os.sep).replace("\\", "/")
        self.artwork_store = artwork_store
        self._source_lock = threading.Lock()
        self._content_lock = threading.Lock()
        self._source_result = None
        self._content_result = None

    def _check_halt(self):
        self.physical._check_halt()

    def _runtime_source_active(self):
        try:
            sources = self.physical._runtime_video_sources()
        except Exception:
            LOGGER.exception("Could not inspect Kodi's active video sources for Prime Movies")
            return None
        if sources is None:
            return None
        wanted = self.physical._normalized_source_path(self.source_url)
        return any(
            self.physical._normalized_source_path(value) == wanted
            for value in sources
        )

    @staticmethod
    def _notify_restart():
        try:
            import xbmcgui

            xbmcgui.Dialog().notification(
                "Otaku Prime",
                "Restart Kodi once to activate the Otaku Prime Movies source",
                time=10000,
            )
        except (ImportError, RuntimeError, AttributeError, TypeError):
            pass

    def ensure_video_source(self):
        """Register library/Movies as a separate Kodi video source."""
        with self._source_lock:
            if self._source_result is not None:
                return dict(self._source_result)
            self._check_halt()
            try:
                if os.path.isfile(self.sources_path):
                    document = ElementTree.parse(self.sources_path)
                else:
                    document = self.physical._new_sources_document()
                root = document.getroot()
                if root.tag != "sources":
                    raise ValueError("sources.xml root element is not <sources>")
                video = root.find("video")
                if video is None:
                    video = ElementTree.SubElement(root, "video")
                    default = ElementTree.SubElement(video, "default")
                    default.set("pathversion", "1")

                wanted = self.physical._normalized_source_path(self.source_url)
                named_source = None
                changed = False
                for source in video.findall("source"):
                    name = str(source.findtext("name") or "").strip()
                    paths = [
                        self.physical._normalized_source_path(node.text)
                        for node in source.findall("path")
                    ]
                    if wanted in paths:
                        active = self._runtime_source_active()
                        self._source_result = {
                            "configured": True,
                            "changed": False,
                            "active": active,
                            "restart_required": active is False,
                            "source": name or MOVIE_SOURCE_NAME,
                            "path": self.source_url,
                        }
                        if active is False:
                            self._notify_restart()
                        return dict(self._source_result)
                    if name == MOVIE_SOURCE_NAME:
                        named_source = source

                source = named_source or ElementTree.SubElement(video, "source")
                for child in list(source):
                    source.remove(child)
                name = ElementTree.SubElement(source, "name")
                name.text = MOVIE_SOURCE_NAME
                path = ElementTree.SubElement(source, "path")
                path.set("pathversion", "1")
                path.text = self.source_url
                sharing = ElementTree.SubElement(source, "allowsharing")
                sharing.text = "true"
                self.physical._write_sources_document(document, self.sources_path)
                changed = True
                active = self._runtime_source_active()
                restart_required = active is False or active is None
                self._source_result = {
                    "configured": True,
                    "changed": changed,
                    "active": active,
                    "restart_required": restart_required,
                    "source": MOVIE_SOURCE_NAME,
                    "path": self.source_url,
                }
                if restart_required:
                    self._notify_restart()
                LOGGER.info("Added Kodi video source %s: %s", MOVIE_SOURCE_NAME, self.source_url)
            except Exception as exc:
                self._source_result = {
                    "configured": False,
                    "changed": False,
                    "active": False,
                    "restart_required": False,
                    "source": MOVIE_SOURCE_NAME,
                    "path": self.source_url,
                    "error": str(exc),
                }
                LOGGER.exception("Prime Physical could not register Kodi Movies source %s", self.source_url)
            return dict(self._source_result)

    def ensure_local_content(self):
        """Set Prime Movies to Movies + Local information only in Kodi."""
        with self._content_lock:
            if self._content_result is not None:
                return dict(self._content_result)
            self._check_halt()
            if not self.video_database_path or not os.path.isfile(self.video_database_path):
                self._content_result = {
                    "configured": False,
                    "changed": False,
                    "content": MOVIE_CONTENT,
                    "scraper": LOCAL_INFORMATION_SCRAPER,
                    "path": self.source_url,
                    "error": "kodi_video_database_unavailable",
                }
                return dict(self._content_result)
            try:
                with sqlite3.connect(self.video_database_path, timeout=3) as db:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(path)")}
                    required = {
                        "strPath", "strContent", "strScraper", "strHash",
                        "scanRecursive", "useFolderNames", "strSettings",
                        "noUpdate", "exclude", "allAudio",
                    }
                    missing = sorted(required - columns)
                    if missing:
                        raise RuntimeError(
                            "unsupported Kodi path schema; missing {}".format(", ".join(missing))
                        )
                    current = db.execute(
                        "SELECT strContent,strScraper,scanRecursive,useFolderNames,"
                        "noUpdate,exclude FROM path WHERE strPath=?",
                        (self.source_url,),
                    ).fetchone()
                    wanted = (MOVIE_CONTENT, LOCAL_INFORMATION_SCRAPER, 0, 0, 0, 0)
                    changed = current != wanted
                    if current is None:
                        db.execute(
                            "INSERT INTO path (strPath,strContent,strScraper,strHash,"
                            "scanRecursive,useFolderNames,strSettings,noUpdate,exclude,allAudio) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (self.source_url, MOVIE_CONTENT, LOCAL_INFORMATION_SCRAPER,
                             "", 0, 0, "", 0, 0, 0),
                        )
                    elif changed:
                        db.execute(
                            "UPDATE path SET strContent=?,strScraper=?,strHash='',"
                            "scanRecursive=0,useFolderNames=0,strSettings='',noUpdate=0,"
                            "exclude=0,allAudio=0 WHERE strPath=?",
                            (MOVIE_CONTENT, LOCAL_INFORMATION_SCRAPER, self.source_url),
                        )
                self._content_result = {
                    "configured": True,
                    "changed": changed,
                    "content": MOVIE_CONTENT,
                    "scraper": LOCAL_INFORMATION_SCRAPER,
                    "path": self.source_url,
                    "database": self.video_database_path,
                }
                LOGGER.info(
                    "Kodi Movies source content configured: path=%s content=%s scraper=%s changed=%s",
                    self.source_url, MOVIE_CONTENT, LOCAL_INFORMATION_SCRAPER, changed,
                )
            except Exception as exc:
                self._content_result = {
                    "configured": False,
                    "changed": False,
                    "content": MOVIE_CONTENT,
                    "scraper": LOCAL_INFORMATION_SCRAPER,
                    "path": self.source_url,
                    "database": self.video_database_path,
                    "error": str(exc),
                }
                LOGGER.exception(
                    "Prime Physical could not set Movies / local information content on %s",
                    self.source_url,
                )
            return dict(self._content_result)

    def ensure_configuration(self):
        return {
            "source": self.ensure_video_source(),
            "content": self.ensure_local_content(),
        }

    def _movie_row(self, movie_id):
        getter = getattr(self.catalog_store, "library_movie_detail", None)
        if getter:
            row = getter(str(movie_id))
            if row:
                return row
        wanted = str(movie_id)
        getter = getattr(self.catalog_store, "list_movies", None)
        rows = getter() if getter else []
        return next((row for row in rows if str(row.get("local_id")) == wanted), None)

    @staticmethod
    def _movie_year(movie):
        value = _integer((movie or {}).get("publish_year"))
        if value:
            return str(value)[:4]
        release = _clean((movie or {}).get("release_date"))
        if release and release[:4].isdigit():
            return release[:4]
        return "Unknown"

    def movie_directory(self, movie_id):
        movie = self._movie_row(movie_id)
        if not movie:
            return None
        title = safe_library_name(
            movie.get("english_name") or movie.get("romaji_name") or movie.get("title"),
            fallback="Untitled {}".format(movie.get("local_id") or movie_id),
        )
        year = self._movie_year(movie)
        return os.path.join(self.root_path, "Movies", "{} {}".format(title, year))

    def _artwork_paths(self, movie):
        if self.artwork_store is None:
            return {}
        ids = {
            provider: movie.get(provider + "_id")
            for provider in PROVIDER_IDS
            if movie.get(provider + "_id") not in (None, "")
        }
        if not ids:
            return {}
        getter = getattr(self.artwork_store, "existing", None)
        if not getter:
            return {}
        try:
            return dict((getter("movies", ids) or {}).get("kodi_paths") or {})
        except Exception as exc:
            LOGGER.warning("Prime Movies could not resolve artwork for %s: %s", movie.get("local_id"), exc)
            return {}

    def _movie_nfo(self, movie, artwork):
        root = ElementTree.Element("movie")
        title = _clean(movie.get("english_name")) or _clean(movie.get("romaji_name")) or "Untitled movie"
        _add_text(root, "title", title)
        romaji = _clean(movie.get("romaji_name"))
        if romaji and romaji != title:
            _add_text(root, "originaltitle", romaji)
        _add_text(root, "plot", movie.get("overview"))
        year = self._movie_year(movie)
        if year.isdigit():
            _add_text(root, "year", year)
        _add_text(root, "premiered", _date_only(movie.get("release_date")))
        runtime = _integer(movie.get("runtime_minutes"))
        if runtime is not None and runtime >= 0:
            _add_text(root, "runtime", runtime)
        _add_text(root, "mpaa", movie.get("age_rating"))
        for genre in _decode_terms(movie.get("genres") or movie.get("genres_json")):
            _add_text(root, "genre", genre)
        for theme in _decode_terms(movie.get("themes") or movie.get("themes_json")):
            _add_text(root, "tag", theme)
        _add_unique_id(root, "prime", movie.get("local_id"), default=True)
        for provider in PROVIDER_IDS:
            _add_unique_id(root, provider, movie.get(provider + "_id"))
        for art_type in MOVIE_ART_TYPES:
            path = _clean(artwork.get(art_type))
            if path:
                node = ElementTree.SubElement(root, "thumb")
                node.set("aspect", art_type)
                node.text = path
        fanart = _clean(artwork.get("fanart"))
        if fanart:
            fanart_node = ElementTree.SubElement(root, "fanart")
            _add_text(fanart_node, "thumb", fanart)
        return _xml_bytes(root)

    def project_movie(self, movie_id, now_epoch):
        """Write one standalone movie folder, STRM and Local Information NFO."""
        self._check_halt()
        self.ensure_configuration()
        movie = self._movie_row(movie_id)
        if not movie:
            return {"movie_id": str(movie_id), "missing": True, "written": 0, "unchanged": 0}
        released = release_epoch(movie.get("release_date"))
        if released and int(released) > int(now_epoch):
            return {
                "movie_id": str(movie["local_id"]), "missing": False,
                "future": True, "written": 0, "unchanged": 0,
            }

        directory = self.movie_directory(movie["local_id"])
        os.makedirs(directory, exist_ok=True)
        title = safe_library_name(
            movie.get("english_name") or movie.get("romaji_name") or movie.get("title"),
            fallback="Untitled {}".format(movie["local_id"]),
        )
        year = self._movie_year(movie)
        stem = "{} {}".format(title, year)
        strm_path = os.path.join(directory, stem + ".strm")
        nfo_path = os.path.join(directory, stem + ".nfo")
        artwork = self._artwork_paths(movie)
        written = unchanged = 0
        if _write_if_changed(strm_path, PLUGIN_BASE + str(movie["local_id"]) + "\n"):
            written += 1
        else:
            unchanged += 1
        if _write_if_changed(nfo_path, self._movie_nfo(movie, artwork)):
            written += 1
        else:
            unchanged += 1
        LOGGER.info(
            "Prime Physical projected movie %s: written=%s unchanged=%s root=%s",
            movie["local_id"], written, unchanged, directory,
        )
        return {
            "movie_id": str(movie["local_id"]),
            "missing": False,
            "future": False,
            "written": written,
            "unchanged": unchanged,
            "directory": directory,
            "strm": strm_path,
            "nfo": nfo_path,
            "artwork": artwork,
        }

    def project_all(self, now_epoch):
        getter = getattr(self.catalog_store, "list_movies", None)
        rows = getter() if getter else []
        result = {"movies": 0, "written": 0, "unchanged": 0, "future": 0, "failed": 0}
        for movie in rows:
            self._check_halt()
            try:
                item = self.project_movie(movie["local_id"], now_epoch)
                result["movies"] += 1
                result["written"] += int(item.get("written") or 0)
                result["unchanged"] += int(item.get("unchanged") or 0)
                result["future"] += int(bool(item.get("future")))
            except Exception:
                result["failed"] += 1
                LOGGER.exception("Prime Physical movie projection failed for %s", movie.get("local_id"))
        LOGGER.info(
            "Prime Physical movie backfill complete: movies=%s written=%s unchanged=%s future=%s failed=%s",
            result["movies"], result["written"], result["unchanged"], result["future"], result["failed"],
        )
        return result
