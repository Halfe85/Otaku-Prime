# -*- coding: utf-8 -*-
"""Write Kodi Local Information NFO files for Prime's physical TV library."""
from __future__ import annotations

import json
import os
import tempfile
import xml.etree.ElementTree as ElementTree

from resources.lib.logging_config import get_logger
from resources.lib.services.prime_physical import safe_library_name
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)


PROVIDER_ARTWORK_IDS = ("tvdb", "simkl", "anilist", "mal", "kitsu")
SERIES_ART_TYPES = ("poster", "banner", "clearlogo", "clearart", "landscape")


def _clean(value):
    text = str(value or "").strip()
    return text or None


def _date_only(value):
    text = _clean(value)
    if not text:
        return None
    return text[:10] if len(text) >= 10 else text


def _integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_field(row, key):
    value = (row or {}).get(key)
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item or "").strip()]
    raw = (row or {}).get(key + "_json")
    if isinstance(raw, str) and raw.strip():
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item or "").strip()]
    return []


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


def _xml_bytes(root):
    tree = ElementTree.ElementTree(root)
    try:
        ElementTree.indent(tree, space="    ")
    except AttributeError:
        pass
    return ElementTree.tostring(
        tree.getroot(), encoding="utf-8", xml_declaration=True
    ) + b"\n"


def _write_if_changed(path, payload):
    try:
        with open(path, "rb") as handle:
            if handle.read() == payload:
                return False
    except FileNotFoundError:
        pass

    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="wb", dir=directory, prefix=".prime-nfo-", suffix=".tmp",
        delete=False,
    )
    temporary = handle.name
    try:
        with handle:
            handle.write(payload)
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


def _series_artwork_ids(series, seasons):
    result = {
        "tvdb": (series or {}).get("tvdb_id"),
        "simkl": (series or {}).get("root_simkl_id"),
        "anilist": (series or {}).get("root_anilist_id"),
        "mal": None,
        "kitsu": None,
    }
    # The catalogue does not currently persist root MAL/Kitsu IDs on tv_series.
    # A season ID is still enough to find the same persistent artwork manifest
    # because ArtworkStore indexes manifests by every provider identity it knows.
    ordered = sorted(
        list(seasons or []),
        key=lambda row: (
            1 if _integer((row or {}).get("season_number")) == 0 else 0,
            _integer((row or {}).get("season_number")) or 0,
        ),
    )
    for season in ordered:
        for provider in PROVIDER_ARTWORK_IDS:
            if result.get(provider) in (None, ""):
                result[provider] = (season or {}).get(provider + "_id")
    return {key: value for key, value in result.items() if value not in (None, "")}


def _episode_artwork_ids(episode):
    return {
        provider: (episode or {}).get(provider + "_id")
        for provider in PROVIDER_ARTWORK_IDS
        if (episode or {}).get(provider + "_id") not in (None, "")
    }


def _artwork_paths(artwork_store, media_type, ids):
    if artwork_store is None or not ids:
        return {}
    getter = getattr(artwork_store, "existing", None)
    if getter is None:
        return {}
    try:
        recovered = getter(media_type, ids) or {}
    except Exception as exc:
        LOGGER.warning(
            "Prime NFO could not resolve %s artwork paths for %s: %s",
            media_type, ids, exc,
        )
        return {}
    paths = recovered.get("kodi_paths") or {}
    return {
        str(key): str(value)
        for key, value in paths.items()
        if value not in (None, "")
    }


def _first_regular_release(seasons):
    regular = []
    fallback = []
    for season in seasons or []:
        value = _date_only((season or {}).get("release_date"))
        if not value:
            continue
        fallback.append(value)
        number = _integer((season or {}).get("season_number"))
        if number is not None and number > 0:
            regular.append(value)
    values = regular or fallback
    return min(values) if values else None


def build_tvshow_nfo(series, seasons, artwork_paths=None):
    """Build one Kodi metadata NFO for the parent TV show."""
    series = series or {}
    seasons = list(seasons or [])
    root = ElementTree.Element("tvshow")
    title = (
        _clean(series.get("english_name"))
        or _clean(series.get("romaji_name"))
        or "Untitled {}".format(series.get("local_id") or "Prime")
    )
    _add_text(root, "title", title)
    romaji = _clean(series.get("romaji_name"))
    if romaji and romaji != title:
        _add_text(root, "originaltitle", romaji)
    _add_text(root, "plot", series.get("overview"))
    year = _integer(series.get("publish_year"))
    if year:
        _add_text(root, "year", year)
    _add_text(root, "premiered", _first_regular_release(seasons))
    _add_text(root, "mpaa", series.get("age_rating"))

    for genre in _list_field(series, "genres"):
        _add_text(root, "genre", genre)
    for theme in _list_field(series, "themes"):
        _add_text(root, "tag", theme)

    _add_unique_id(root, "prime", series.get("local_id"), default=True)
    _add_unique_id(root, "anilist", series.get("root_anilist_id"))
    _add_unique_id(root, "simkl", series.get("root_simkl_id"))
    _add_unique_id(root, "tvdb", series.get("tvdb_id"))

    art = dict(artwork_paths or {})
    for art_type in SERIES_ART_TYPES:
        path = _clean(art.get(art_type))
        if not path:
            continue
        node = ElementTree.SubElement(root, "thumb")
        node.set("aspect", art_type)
        node.text = path
    fanart = _clean(art.get("fanart"))
    if fanart:
        fanart_node = ElementTree.SubElement(root, "fanart")
        _add_text(fanart_node, "thumb", fanart)
    return _xml_bytes(root)


def build_episode_nfo(series, season, episode, artwork_paths=None):
    """Build one Kodi episode NFO matching an adjacent STRM file."""
    series = series or {}
    season = season or {}
    episode = episode or {}
    root = ElementTree.Element("episodedetails")
    number = _integer(episode.get("episode_number")) or 1
    season_number = _integer(season.get("season_number")) or 0
    title = _clean(episode.get("title")) or "Episode {}".format(number)
    show_title = (
        _clean(series.get("english_name"))
        or _clean(series.get("romaji_name"))
        or "Untitled"
    )

    _add_text(root, "title", title)
    _add_text(root, "showtitle", show_title)
    _add_text(root, "season", season_number)
    _add_text(root, "episode", number)
    _add_text(root, "plot", episode.get("overview"))
    runtime = _integer(episode.get("runtime_minutes"))
    if runtime is not None and runtime >= 0:
        _add_text(root, "runtime", runtime)
    _add_text(root, "aired", _date_only(episode.get("release_date")))
    _add_text(root, "playcount", 1 if bool(episode.get("watch_status")) else 0)
    _add_unique_id(root, "prime", episode.get("local_id"), default=True)

    # Episode/provider IDs in Prime are often media-entry IDs rather than true
    # episode IDs, so only Prime's opaque episode ID is exported as a Kodi
    # uniqueid. That avoids duplicate external unique IDs across a season.
    thumb = _clean((artwork_paths or {}).get("thumb"))
    if thumb:
        node = ElementTree.SubElement(root, "thumb")
        node.set("aspect", "thumb")
        node.text = thumb
    return _xml_bytes(root)


class PrimeNfoWriter:
    """Project catalogue metadata into tvshow.nfo and adjacent episode NFOs."""

    def __init__(self, catalog_store, artwork_store=None):
        self.catalog_store = catalog_store
        self.artwork_store = artwork_store

    def write_series(self, series_id, series_directory, now_epoch):
        getter = getattr(self.catalog_store, "get_series", None)
        if getter:
            series = getter(str(series_id))
        else:
            wanted = str(series_id)
            series = next(
                (row for row in self.catalog_store.list_series()
                 if str(row.get("local_id")) == wanted),
                None,
            )
        if not series:
            return {
                "written": 0, "unchanged": 0, "episodes": 0,
                "missing": True, "artwork": {},
            }

        seasons = self.catalog_store.list_seasons(series["local_id"])
        title = safe_library_name(
            series.get("english_name") or series.get("romaji_name"),
            fallback="Untitled {}".format(series["local_id"]),
        )
        written = unchanged = episodes_written = 0
        eligible_episode_files = 0

        for season in seasons:
            season_number = _integer(season.get("season_number"))
            if season_number is None or season_number < 0:
                continue
            for episode in self.catalog_store.list_episodes(season["local_id"]):
                episode_number = _integer(episode.get("episode_number"))
                if episode_number is None or episode_number <= 0:
                    continue
                released = release_epoch(episode.get("release_date"))
                if not released or int(released) > int(now_epoch):
                    continue
                season_directory = os.path.join(
                    series_directory, "Season {:02d}".format(season_number)
                )
                stem = "{} - S{:02d}E{:02d}".format(
                    title, season_number, episode_number
                )
                strm_path = os.path.join(season_directory, stem + ".strm")
                if not os.path.isfile(strm_path):
                    continue
                eligible_episode_files += 1
                episode_art = _artwork_paths(
                    self.artwork_store, "episodes", _episode_artwork_ids(episode)
                )
                changed = _write_if_changed(
                    os.path.join(season_directory, stem + ".nfo"),
                    build_episode_nfo(series, season, episode, episode_art),
                )
                if changed:
                    written += 1
                    episodes_written += 1
                else:
                    unchanged += 1

        if eligible_episode_files:
            series_art = _artwork_paths(
                self.artwork_store,
                "tvshows",
                _series_artwork_ids(series, seasons),
            )
            changed = _write_if_changed(
                os.path.join(series_directory, "tvshow.nfo"),
                build_tvshow_nfo(series, seasons, series_art),
            )
            if changed:
                written += 1
            else:
                unchanged += 1
        else:
            series_art = {}

        LOGGER.info(
            "Prime NFO projected series %s: written=%s unchanged=%s episodes=%s art=%s",
            series.get("local_id"), written, unchanged, episodes_written,
            ",".join(sorted(series_art)) or "none",
        )
        return {
            "written": written,
            "unchanged": unchanged,
            "episodes": episodes_written,
            "missing": False,
            "artwork": series_art,
        }
