# -*- coding: utf-8 -*-
"""Mature-artwork policy shared by Prime's native Kodi library projection."""
from __future__ import annotations

import json
import os
import tempfile
import threading

from resources.lib.logging_config import get_logger


LOGGER = get_logger(__name__)
BLURRED_ART_TYPES = {"poster", "fanart", "banner", "landscape", "thumb", "clearart"}
PROVIDER_IDS = ("tvdb", "simkl", "anilist", "mal", "kitsu")


def _terms(value):
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        if isinstance(decoded, list):
            return [str(item).strip() for item in decoded if str(item or "").strip()]
    return []


def has_hentai_genre(row):
    """Mirror the web Library's mature-artwork classification exactly."""
    row = row or {}
    values = _terms(row.get("genres")) or _terms(row.get("genres_json"))
    return any(value.lower() == "hentai" for value in values)


def _row_ids(row, root=False):
    row = row or {}
    result = {}
    for provider in PROVIDER_IDS:
        keys = [provider + "_id"]
        if root:
            keys.insert(0, "root_" + provider + "_id")
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                result[provider] = str(value)
                break
    return result


def _ids_overlap(left, right):
    return any(
        provider in left and provider in right and str(left[provider]) == str(right[provider])
        for provider in PROVIDER_IDS
    )


class MatureAwareArtworkStore:
    """Proxy Prime artwork and substitute blurred Kodi paths for Hentai titles.

    Browser URLs remain untouched; the Prime web Library keeps using its CSS blur.
    Only ``kodi_paths`` are rewritten, so Kodi's native library receives pre-blurred
    files while the mature-content preference is disabled.
    """

    def __init__(self, artwork_store, catalog_store, preference_getter=None,
                 blur_path=None):
        self._store = artwork_store
        self.catalog_store = catalog_store
        self.preference_getter = preference_getter or (lambda: 0)
        self._blur_path_override = blur_path
        self._lock = threading.RLock()
        self._failed_pillow_logged = False

    def __getattr__(self, name):
        return getattr(self._store, name)

    def mature_enabled(self):
        try:
            return 1 if int(self.preference_getter() or 0) == 1 else 0
        except Exception:
            LOGGER.exception("Could not read Prime mature-content preference; using protected artwork")
            return 0

    def _series_rows(self):
        getter = getattr(self.catalog_store, "list_series", None)
        return list(getter() or []) if getter else []

    def _movie_rows(self):
        getter = getattr(self.catalog_store, "list_movies", None)
        return list(getter() or []) if getter else []

    def mature_series_ids(self):
        return [
            str(row.get("local_id"))
            for row in self._series_rows()
            if row.get("local_id") not in (None, "") and has_hentai_genre(row)
        ]

    def mature_movie_ids(self):
        return [
            str(row.get("local_id"))
            for row in self._movie_rows()
            if row.get("local_id") not in (None, "") and has_hentai_genre(row)
        ]

    def _series_is_mature(self, ids):
        wanted = {key: str(value) for key, value in (ids or {}).items()
                  if key in PROVIDER_IDS and value not in (None, "")}
        if not wanted:
            return False
        for series in self._series_rows():
            if not has_hentai_genre(series):
                continue
            candidates = _row_ids(series, root=True)
            seasons_getter = getattr(self.catalog_store, "list_seasons", None)
            seasons = seasons_getter(series.get("local_id")) if seasons_getter else []
            for season in seasons or []:
                candidates.update({
                    key: value for key, value in _row_ids(season).items()
                    if key not in candidates
                })
                if _ids_overlap(wanted, _row_ids(season)):
                    return True
            if _ids_overlap(wanted, candidates):
                return True
        return False

    def _movie_is_mature(self, ids):
        wanted = {key: str(value) for key, value in (ids or {}).items()
                  if key in PROVIDER_IDS and value not in (None, "")}
        if not wanted:
            return False
        return any(
            has_hentai_genre(movie) and _ids_overlap(wanted, _row_ids(movie))
            for movie in self._movie_rows()
        )

    def _is_mature(self, media_type, ids):
        media_type = str(media_type or "").strip().lower()
        if media_type == "tvshows":
            return self._series_is_mature(ids)
        if media_type == "movies":
            return self._movie_is_mature(ids)
        return False

    def _relative_from_kodi_path(self, kodi_path):
        prefix = str(getattr(self._store, "special_root", "")).rstrip("/") + "/"
        value = str(kodi_path or "")
        if not prefix.strip("/") or not value.startswith(prefix):
            return None
        return value[len(prefix):].replace("\\", "/").lstrip("/")

    def _blurred_kodi_path(self, kodi_path):
        if self._blur_path_override is not None:
            return self._blur_path_override(kodi_path)

        relative = self._relative_from_kodi_path(kodi_path)
        root = os.path.abspath(str(getattr(self._store, "root_path", "") or ""))
        if not relative or not root:
            raise RuntimeError("artwork path is outside Prime's persistent artwork store")
        source = os.path.abspath(os.path.join(root, *relative.split("/")))
        if os.path.commonpath((root, source)) != root or not os.path.isfile(source):
            raise RuntimeError("Prime artwork source is missing")

        stem, extension = os.path.splitext(os.path.basename(relative))
        extension = extension.lower() or ".png"
        blurred_relative = os.path.join(
            os.path.dirname(relative), "blurred", stem + "-prime-blur" + extension
        ).replace(os.sep, "/")
        target = os.path.abspath(os.path.join(root, *blurred_relative.split("/")))
        if os.path.isfile(target) and os.path.getsize(target) > 0:
            return self._store.kodi_path(blurred_relative)

        try:
            from PIL import Image, ImageFilter
        except (ImportError, RuntimeError) as exc:
            if not self._failed_pillow_logged:
                self._failed_pillow_logged = True
                LOGGER.error(
                    "Kodi mature artwork blur is unavailable because script.module.pil/Pillow "
                    "could not be loaded: %s",
                    exc,
                )
            raise RuntimeError("Pillow unavailable") from exc

        os.makedirs(os.path.dirname(target), exist_ok=True)
        temporary = None
        try:
            with Image.open(source) as image:
                source_format = image.format or {
                    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
                    ".webp": "WEBP", ".gif": "GIF",
                }.get(extension, "PNG")
                working = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
                radius = max(18, min(72, int(round(min(working.size) * 0.06))))
                blurred = working.filter(ImageFilter.GaussianBlur(radius=radius))
                handle = tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=os.path.dirname(target),
                    prefix=".prime-blur-",
                    suffix=extension,
                    delete=False,
                )
                temporary = handle.name
                handle.close()
                save_image = blurred
                if source_format.upper() == "JPEG" and save_image.mode != "RGB":
                    save_image = save_image.convert("RGB")
                save_image.save(temporary, format=source_format)
                os.replace(temporary, target)
                temporary = None
                LOGGER.info(
                    "Prime mature artwork blur generated: source=%s target=%s radius=%s",
                    source,
                    target,
                    radius,
                )
        finally:
            if temporary:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
        return self._store.kodi_path(blurred_relative)

    def existing(self, media_type, ids):
        result = dict(self._store.existing(media_type, ids) or {})
        if self.mature_enabled() or not self._is_mature(media_type, ids):
            return result

        original = dict(result.get("kodi_paths") or {})
        protected = dict(original)
        for art_type in sorted(BLURRED_ART_TYPES):
            path = original.get(art_type)
            if not path:
                continue
            try:
                protected[art_type] = self._blurred_kodi_path(path)
            except Exception as exc:
                # Fail closed: when protection is requested, never hand Kodi the
                # original sensitive artwork if a derivative cannot be generated.
                protected.pop(art_type, None)
                LOGGER.warning(
                    "Prime mature artwork hidden because blur generation failed: "
                    "media=%s art=%s error=%s",
                    media_type,
                    art_type,
                    exc,
                )
        result["kodi_paths"] = protected
        return result
