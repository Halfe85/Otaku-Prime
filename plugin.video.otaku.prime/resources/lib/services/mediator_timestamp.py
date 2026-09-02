# -*- coding: utf-8 -*-
"""Fetch and normalize per-episode skip metadata for Prime mediation."""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from resources.lib.logging_config import get_logger
from resources.lib.services.watchlist_release import release_epoch


LOGGER = get_logger(__name__)
ANISKIP_URL = "https://api.aniskip.com/v2/skip-times/{mal_id}/{episode}"
THEINTRODB_URL = "https://api.theintrodb.org/v3/media"
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Otaku-Prime/0.1.2",
}
ANISKIP_TYPE_MAP = {
    "op": "intro",
    "mixed-op": "intro",
    "ed": "credits",
    "mixed-ed": "credits",
    "recap": "recap",
}
THEINTRODB_TYPES = ("intro", "recap", "credits", "preview")


class TimestampProviderError(RuntimeError):
    pass


def _millisecond(value):
    if value is None:
        return None
    try:
        return max(0, int(round(float(value) * 1000.0)))
    except (TypeError, ValueError):
        return None


def _json_get(url, timeout, opener):
    request = Request(url, headers=HEADERS, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            status = int(getattr(response, "status", 200) or 200)
            payload = json.loads(response.read().decode("utf-8"))
            return status, payload
    except HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {}
        return int(exc.code), payload
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        raise TimestampProviderError(str(exc)) from exc


class AniSkipTimestampClient:
    def __init__(self, timeout=8, opener=None):
        self.timeout = max(1, int(timeout))
        self._open = opener or urlopen

    def fetch(self, mal_id, episode_number, episode_length_seconds=0):
        mal_id = int(mal_id)
        episode_number = int(episode_number)
        query = urlencode([
            ("episodeLength", int(episode_length_seconds or 0)),
            ("types", "op"),
            ("types", "ed"),
            ("types", "recap"),
        ])
        url = ANISKIP_URL.format(mal_id=mal_id, episode=episode_number) + "?" + query
        LOGGER.info(
            "Timestamp mediator AniSkip request started: mal=%s episode=%s duration=%ss",
            mal_id, episode_number, int(episode_length_seconds or 0),
        )
        status, payload = _json_get(url, self.timeout, self._open)
        if status == 404 or not bool((payload or {}).get("found")):
            LOGGER.info(
                "Timestamp mediator AniSkip has no data: mal=%s episode=%s",
                mal_id, episode_number,
            )
            return []
        if status != 200:
            raise TimestampProviderError("AniSkip returned HTTP {}".format(status))

        segments = []
        seen = set()
        for result in (payload or {}).get("results") or []:
            skip_type = str(result.get("skipType") or "").strip().lower()
            segment_type = ANISKIP_TYPE_MAP.get(skip_type)
            interval = result.get("interval") or {}
            start_ms = _millisecond(interval.get("startTime"))
            end_ms = _millisecond(interval.get("endTime"))
            if not segment_type or start_ms is None or end_ms is None or end_ms <= start_ms:
                continue
            source_duration_ms = _millisecond(result.get("episodeLength"))
            key = (segment_type, start_ms, end_ms)
            if key in seen:
                continue
            seen.add(key)
            segments.append({
                "type": segment_type,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source": "aniskip",
                "source_duration_ms": source_duration_ms,
                "source_ref": str(result.get("skipId") or result.get("id") or "") or None,
            })
        LOGGER.info(
            "Timestamp mediator AniSkip request complete: mal=%s episode=%s segments=%s",
            mal_id, episode_number, len(segments),
        )
        return segments


class TheIntroDBTimestampClient:
    def __init__(self, timeout=8, opener=None):
        self.timeout = max(1, int(timeout))
        self._open = opener or urlopen

    def fetch(self, tvdb_id, season_number, episode_number):
        query = urlencode({
            "tvdb_id": str(tvdb_id),
            "season": int(season_number),
            "episode": int(episode_number),
        })
        url = THEINTRODB_URL + "?" + query
        LOGGER.info(
            "Timestamp mediator TheIntroDB request started: tvdb=%s S%02dE%02d",
            tvdb_id, int(season_number), int(episode_number),
        )
        status, payload = _json_get(url, self.timeout, self._open)
        if status == 404:
            LOGGER.info(
                "Timestamp mediator TheIntroDB has no data: tvdb=%s S%02dE%02d",
                tvdb_id, int(season_number), int(episode_number),
            )
            return []
        if status != 200:
            raise TimestampProviderError("TheIntroDB returned HTTP {}".format(status))

        segments = []
        for segment_type in THEINTRODB_TYPES:
            for entry in (payload or {}).get(segment_type) or []:
                raw_start = entry.get("start_ms")
                raw_end = entry.get("end_ms")
                try:
                    start_ms = 0 if raw_start is None else max(0, int(raw_start))
                    end_ms = None if raw_end is None else max(0, int(raw_end))
                except (TypeError, ValueError):
                    continue
                if end_ms is not None and end_ms <= start_ms:
                    continue
                segments.append({
                    "type": segment_type,
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "source": "theintrodb",
                    "source_duration_ms": None,
                    "source_ref": str((payload or {}).get("tmdb_id") or "") or None,
                })
        LOGGER.info(
            "Timestamp mediator TheIntroDB request complete: tvdb=%s S%02dE%02d segments=%s",
            tvdb_id, int(season_number), int(episode_number), len(segments),
        )
        return segments


class MediatorTimestampService:
    """Queue timestamp enrichment without blocking the main placement worker."""

    def __init__(self, catalog_store, timeout=8, halt_requested=None,
                 aniskip=None, theintrodb=None, sleep=None):
        self.catalog_store = catalog_store
        self.timeout = max(1, int(timeout))
        self._halt_requested = halt_requested or (lambda: False)
        self.aniskip = aniskip or AniSkipTimestampClient(timeout=self.timeout)
        self.theintrodb = theintrodb or TheIntroDBTimestampClient(timeout=self.timeout)
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._pending = []
        self._pending_ids = set()
        self._thread = None
        self._stop = threading.Event()

    def _halted(self):
        return self._stop.is_set() or self._halt_requested()

    def schedule_watchlist_item(self, item, series_id=None, force=False):
        getter = getattr(self.catalog_store, "timestamp_contexts_for_watchlist", None)
        if not getter:
            LOGGER.warning(
                "Timestamp mediator cannot schedule Prime item %s: catalogue timestamp API unavailable",
                (item or {}).get("local_id"),
            )
            return {"scheduled": 0, "available": False}
        contexts = getter(
            (item or {}).get("local_id"),
            series_id=series_id,
            force=force,
        )
        queued = 0
        with self._lock:
            for context in contexts:
                episode_id = str(context.get("episode_local_id") or "")
                if not episode_id or episode_id in self._pending_ids:
                    continue
                self._pending.append(dict(context))
                self._pending_ids.add(episode_id)
                queued += 1
            if queued and (not self._thread or not self._thread.is_alive()):
                self._stop.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    name="OtakuPrimeMediatorTimestamp",
                    daemon=True,
                )
                self._thread.start()
        if queued:
            LOGGER.info(
                "Timestamp mediator queued Prime item %s: episodes=%s",
                (item or {}).get("local_id"), queued,
            )
        return {"scheduled": queued, "available": True}

    def _run(self):
        while not self._halted():
            with self._lock:
                if not self._pending:
                    return
                context = self._pending.pop(0)
            episode_id = str(context.get("episode_local_id") or "")
            try:
                self._enrich_episode(context)
            except Exception as exc:
                LOGGER.exception(
                    "Timestamp mediator failed for Prime episode %s", episode_id
                )
                recorder = getattr(self.catalog_store, "record_episode_timestamp_error", None)
                if recorder:
                    recorder(episode_id, str(exc))
            finally:
                with self._lock:
                    self._pending_ids.discard(episode_id)
            # AniSkip's public limit is 120 requests/minute. The half-second
            # cadence keeps this worker within that ceiling even on large shows.
            if not self._halted():
                self._sleep(0.5)

    def _enrich_episode(self, context):
        episode_id = str(context["episode_local_id"])
        release = release_epoch(context.get("release_date"))
        if release and int(release) > int(time.time()):
            return

        segments = []
        errors = []
        mal_id = context.get("timestamp_mal_id")
        source_episode = int(context.get("source_episode_number") or 1)
        runtime_minutes = context.get("runtime_minutes")
        duration_seconds = 0
        try:
            if runtime_minutes not in (None, ""):
                duration_seconds = max(0, int(runtime_minutes) * 60)
        except (TypeError, ValueError):
            duration_seconds = 0

        if mal_id not in (None, ""):
            try:
                segments = self.aniskip.fetch(
                    mal_id, source_episode, episode_length_seconds=duration_seconds
                )
                # Some AniSkip rows are stored without a duration-compatible
                # variant. Retry once without duration before using the fallback.
                if not segments and duration_seconds:
                    segments = self.aniskip.fetch(
                        mal_id, source_episode, episode_length_seconds=0
                    )
            except Exception as exc:
                errors.append("aniskip: {}".format(exc))
                LOGGER.warning(
                    "Timestamp mediator AniSkip unavailable for Prime episode %s: %s",
                    episode_id, exc,
                )

        # TheIntroDB is a fallback rather than a second request for every anime
        # episode. This protects its public daily quota and avoids duplicate
        # segments when AniSkip already has a usable result.
        if not segments and context.get("tvdb_id") not in (None, ""):
            season_number = context.get("timestamp_season_number")
            episode_number = context.get("timestamp_episode_number")
            if season_number is not None and episode_number is not None:
                try:
                    segments = self.theintrodb.fetch(
                        context["tvdb_id"], season_number, episode_number
                    )
                except Exception as exc:
                    errors.append("theintrodb: {}".format(exc))
                    LOGGER.warning(
                        "Timestamp mediator TheIntroDB unavailable for Prime episode %s: %s",
                        episode_id, exc,
                    )

        replacer = getattr(self.catalog_store, "replace_episode_segments", None)
        if not replacer:
            return
        if segments:
            result = replacer(episode_id, segments, status="FOUND", error=None)
            sources = sorted({segment.get("source") for segment in segments if segment.get("source")})
            LOGGER.info(
                "Timestamp mediator stored Prime episode %s: segments=%s sources=%s",
                episode_id, result.get("segment_count", len(segments)),
                ",".join(sources) or "unknown",
            )
        elif errors:
            self.catalog_store.record_episode_timestamp_error(
                episode_id, "; ".join(errors)
            )
        else:
            result = replacer(episode_id, [], status="EMPTY", error=None)
            LOGGER.info(
                "Timestamp mediator found no skip metadata for Prime episode %s",
                episode_id,
            )

    def request_stop(self):
        self._stop.set()

    def stop(self, timeout=2):
        self.request_stop()
        thread = self._thread
        if thread:
            thread.join(timeout=max(0.0, float(timeout)))
        return not bool(thread and thread.is_alive())
