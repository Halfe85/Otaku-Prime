# -*- coding: utf-8 -*-
"""Project released Prime catalogue episodes into Kodi's physical library."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ElementTree

from resources.lib.logging_config import get_logger
from resources.lib.service_lifecycle import ServiceWorkHalted
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)
SPECIAL_ROOT = "special://profile/Library"
SOURCES_SPECIAL_PATH = "special://profile/sources.xml"
SOURCE_NAME = "Otaku Prime TV-Series"
INVALID_PATH_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _default_root():
    try:
        import xbmcvfs

        translated = xbmcvfs.translatePath(SPECIAL_ROOT)
        if translated:
            return translated
    except (ImportError, RuntimeError, AttributeError):
        pass
    return os.path.join(os.path.expanduser("~"), ".kodi", "userdata", "Library")


def _default_sources_path():
    try:
        import xbmcvfs

        translated = xbmcvfs.translatePath(SOURCES_SPECIAL_PATH)
        if translated:
            return translated
    except (ImportError, RuntimeError, AttributeError):
        pass
    return os.path.join(os.path.expanduser("~"), ".kodi", "userdata", "sources.xml")


def _kodi_runtime_video_sources():
    """Return Kodi's currently loaded video source paths when available."""
    try:
        import xbmc

        request = json.dumps({
            "jsonrpc": "2.0",
            "method": "Files.GetSources",
            "params": {"media": "video"},
            "id": 1,
        })
        response = json.loads(xbmc.executeJSONRPC(request))
        return [
            row.get("file")
            for row in response.get("result", {}).get("sources", [])
            if row.get("file")
        ]
    except (ImportError, RuntimeError, AttributeError, TypeError, ValueError):
        return None


def _notify_source_restart_required():
    try:
        import xbmcgui

        xbmcgui.Dialog().notification(
            "Otaku Prime",
            "Restart Kodi once to activate the Otaku Prime TV source",
            time=10000,
        )
    except (ImportError, RuntimeError, AttributeError, TypeError):
        pass


def safe_library_name(value, fallback="Untitled"):
    """Return one portable path component without changing readable titles."""
    text = INVALID_PATH_CHARACTERS.sub(" - ", str(value or "").strip())
    text = " ".join(text.split()).strip(" .")
    return (text or str(fallback)).strip()[:180].rstrip(" .")


class PrimePhysicalService:
    """Create zero-byte STRM placeholders from catalogue IDs handed off by Mediator.

    Prime Physical deliberately receives only a Prime series ID. It reads the
    series, season, episode, title, year, and release data from the catalogue so
    the filesystem layer never depends on provider placement payloads.
    """

    def __init__(self, catalog_store, root_path=None, now=None, halt_requested=None,
                 sources_path=None, source_url=None, runtime_video_sources=None,
                 notify_source_restart=None):
        self.catalog_store = catalog_store
        kodi_default_root = root_path is None
        self.root_path = os.path.abspath(str(root_path or _default_root()))
        self.sources_path = os.path.abspath(str(
            sources_path or (
                _default_sources_path() if kodi_default_root
                else os.path.join(self.root_path, "sources.xml")
            )
        ))
        # Store Kodi's translated local path rather than a special:// alias.
        # This is portable across Kodi platforms because each installation
        # writes its own resolved profile location, and it is also what Kodi's
        # scanner and Files.GetSources expose at runtime.
        self.source_url = str(
            source_url or os.path.join(self.root_path, "TV-Series") + os.sep
        ).replace("\\", "/")
        self._now = now or time.time
        self._halt_requested = halt_requested or (lambda: False)
        self._runtime_video_sources = (
            runtime_video_sources or _kodi_runtime_video_sources
        )
        self._notify_source_restart = (
            notify_source_restart or _notify_source_restart_required
        )
        self._source_lock = threading.Lock()
        self._source_result = None

    def _check_halt(self):
        if self._halt_requested():
            raise ServiceWorkHalted("physical library projection halted for addon shutdown")

    @staticmethod
    def _normalized_source_path(value):
        return str(value or "").strip().replace("\\", "/").rstrip("/")

    @staticmethod
    def _new_sources_document():
        root = ElementTree.Element("sources")
        for section_name in ("programs", "video", "music", "pictures", "files", "games"):
            section = ElementTree.SubElement(root, section_name)
            default = ElementTree.SubElement(section, "default")
            default.set("pathversion", "1")
        return ElementTree.ElementTree(root)

    def _runtime_source_active(self):
        try:
            sources = self._runtime_video_sources()
        except Exception:
            LOGGER.exception("Could not inspect Kodi's active video sources")
            return None
        if sources is None:
            return None
        wanted = self._normalized_source_path(self.source_url)
        return any(
            self._normalized_source_path(value) == wanted
            for value in sources
        )

    def _source_result_for(self, changed, source):
        active = self._runtime_source_active()
        # A Kodi service always runs after Kodi loaded sources.xml. Therefore a
        # newly written source needs one restart before it can be active. When
        # JSON-RPC is unavailable (unit tests/non-Kodi tools), changed remains
        # enough evidence for the restart requirement.
        restart_required = active is False or (active is None and changed)
        result = {
            "configured": True,
            "changed": bool(changed),
            "active": active,
            "restart_required": restart_required,
            "source": source or SOURCE_NAME,
            "path": self.source_url,
        }
        if restart_required:
            LOGGER.warning(
                "Kodi restart required to load video source %s: %s",
                SOURCE_NAME,
                self.source_url,
            )
            self._notify_source_restart()
        elif active:
            LOGGER.info(
                "Kodi video source is active in the running profile: %s",
                self.source_url,
            )
        return result

    @staticmethod
    def _write_sources_document(document, target):
        directory = os.path.dirname(target)
        os.makedirs(directory, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=".sources-", suffix=".xml.tmp",
            delete=False,
        )
        temporary = handle.name
        try:
            with handle:
                ElementTree.indent(document, space="    ")
                document.write(handle, encoding="utf-8", xml_declaration=False)
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise

    def ensure_video_source(self):
        """Register Prime's TV-Series folder without replacing Kodi sources."""
        with self._source_lock:
            if self._source_result is not None:
                return dict(self._source_result)
            self._check_halt()
            try:
                if os.path.isfile(self.sources_path):
                    document = ElementTree.parse(self.sources_path)
                else:
                    document = self._new_sources_document()
                root = document.getroot()
                if root.tag != "sources":
                    raise ValueError("sources.xml root element is not <sources>")
                video = root.find("video")
                if video is None:
                    video = ElementTree.SubElement(root, "video")
                    default = ElementTree.SubElement(video, "default")
                    default.set("pathversion", "1")

                wanted = self._normalized_source_path(self.source_url)
                named_source = None
                for source in video.findall("source"):
                    name = str(source.findtext("name") or "").strip()
                    paths = [self._normalized_source_path(node.text)
                             for node in source.findall("path")]
                    if wanted in paths:
                        self._source_result = self._source_result_for(
                            False, name or SOURCE_NAME
                        )
                        LOGGER.info(
                            "Kodi video source already contains Prime TV library: %s",
                            self.source_url,
                        )
                        return dict(self._source_result)
                    if name == SOURCE_NAME:
                        named_source = source

                source = named_source or ElementTree.SubElement(video, "source")
                for child in list(source):
                    source.remove(child)
                name = ElementTree.SubElement(source, "name")
                name.text = SOURCE_NAME
                path = ElementTree.SubElement(source, "path")
                path.set("pathversion", "1")
                path.text = self.source_url
                sharing = ElementTree.SubElement(source, "allowsharing")
                sharing.text = "true"
                self._write_sources_document(document, self.sources_path)
                self._source_result = self._source_result_for(True, SOURCE_NAME)
                LOGGER.info(
                    "Added Kodi video source %s: %s", SOURCE_NAME, self.source_url
                )
            except ServiceWorkHalted:
                raise
            except (OSError, ValueError, ElementTree.ParseError) as exc:
                self._source_result = {
                    "configured": False, "changed": False,
                    "active": False, "restart_required": False,
                    "source": SOURCE_NAME, "path": self.source_url,
                    "error": str(exc),
                }
                LOGGER.exception(
                    "Prime Physical could not register Kodi video source %s",
                    self.source_url,
                )
            return dict(self._source_result)

    @staticmethod
    def _series_year(series, seasons):
        year = series.get("publish_year")
        if year not in (None, "") and str(year)[:4].isdigit():
            return str(year)[:4]
        dates = sorted(
            str(season.get("release_date"))
            for season in seasons
            if str(season.get("release_date") or "")[:4].isdigit()
        )
        return dates[0][:4] if dates else "Unknown"

    @staticmethod
    def _released(episode, now_epoch):
        epoch = release_epoch((episode or {}).get("release_date"))
        return bool(epoch and epoch <= int(now_epoch))

    def _series_row(self, series_id):
        getter = getattr(self.catalog_store, "get_series", None)
        if getter:
            return getter(str(series_id))
        wanted = str(series_id)
        return next(
            (series for series in self.catalog_store.list_series()
             if str(series.get("local_id")) == wanted),
            None,
        )

    def project_series(self, series_id, _log_result=True):
        """Create missing released episode placeholders for one Prime series ID."""
        self._check_halt()
        self.ensure_video_source()
        series = self._series_row(series_id)
        if not series:
            LOGGER.warning("Prime Physical handoff ignored unknown series ID %s", series_id)
            return {
                "series_id": str(series_id), "created": 0, "existing": 0,
                "future": 0, "unknown_release": 0, "failed": 0,
                "missing": True,
            }

        seasons = self.catalog_store.list_seasons(series["local_id"])
        title = safe_library_name(
            series.get("english_name") or series.get("romaji_name"),
            fallback="Untitled {}".format(series["local_id"]),
        )
        year = self._series_year(series, seasons)
        series_directory = os.path.join(
            self.root_path, "TV-Series", "{} {}".format(title, year)
        )
        now_epoch = int(self._now())
        result = {
            "series_id": str(series["local_id"]), "created": 0, "existing": 0,
            "future": 0, "unknown_release": 0, "failed": 0,
            "missing": False,
        }

        for season in seasons:
            self._check_halt()
            try:
                season_number = int(season.get("season_number"))
            except (TypeError, ValueError):
                LOGGER.warning(
                    "Prime Physical skipped invalid season coordinate for series %s: %r",
                    series["local_id"], season.get("season_number"),
                )
                result["failed"] += 1
                continue
            for episode in self.catalog_store.list_episodes(season["local_id"]):
                self._check_halt()
                release_value = episode.get("release_date")
                release_value_epoch = release_epoch(release_value)
                if not release_value_epoch:
                    result["unknown_release"] += 1
                    continue
                if not self._released(episode, now_epoch):
                    result["future"] += 1
                    continue
                try:
                    episode_number = int(episode.get("episode_number"))
                except (TypeError, ValueError):
                    LOGGER.warning(
                        "Prime Physical skipped invalid episode coordinate for series %s: %r",
                        series["local_id"], episode.get("episode_number"),
                    )
                    result["failed"] += 1
                    continue

                season_directory = os.path.join(
                    series_directory, "Season {:02d}".format(season_number)
                )
                filename = "{} - S{:02d}E{:02d}.strm".format(
                    title, season_number, episode_number
                )
                target = os.path.join(season_directory, filename)
                try:
                    os.makedirs(season_directory, exist_ok=True)
                    # Never truncate a future playable STRM body. Exclusive
                    # creation keeps this placeholder stage forward-compatible.
                    with open(target, "xb"):
                        pass
                    result["created"] += 1
                except FileExistsError:
                    result["existing"] += 1
                except OSError:
                    result["failed"] += 1
                    LOGGER.exception(
                        "Prime Physical could not create STRM placeholder for %s S%02dE%02d",
                        series["local_id"], season_number, episode_number,
                    )

        if _log_result:
            LOGGER.info(
                "Prime Physical projected series %s: created=%s existing=%s future=%s "
                "unknown_release=%s failed=%s root=%s",
                series["local_id"], result["created"], result["existing"],
                result["future"], result["unknown_release"], result["failed"],
                series_directory,
            )
        return result

    def project_all(self):
        """Backfill existing catalogue series after installing this service."""
        self.ensure_video_source()
        total = {"series": 0, "created": 0, "existing": 0, "future": 0,
                 "unknown_release": 0, "failed": 0}
        for series in self.catalog_store.list_series():
            self._check_halt()
            result = self.project_series(series["local_id"], _log_result=False)
            total["series"] += 1
            for field in ("created", "existing", "future", "unknown_release", "failed"):
                total[field] += int(result.get(field) or 0)
        LOGGER.info(
            "Prime Physical catalogue backfill complete: series=%s created=%s existing=%s "
            "future=%s unknown_release=%s failed=%s",
            total["series"], total["created"], total["existing"], total["future"],
            total["unknown_release"], total["failed"],
        )
        return total
