# -*- coding: utf-8 -*-
"""Resolve Prime franchises/seasons/episodes against the selected Kodi metadata source."""
from __future__ import annotations

import datetime
import json
import re
import time
from resources.lib.logging_config import get_logger
LOGGER=get_logger(__name__)
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from resources.lib.database.metadata_provider import (
    KODI_SCRAPER_ADDONS,
    SUPPORTED_PROVIDERS,
)


class MetadataProviderError(RuntimeError):
    pass


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _title_variants(value):
    """Return the original title plus safe provider-search base variants."""
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    if not title:
        return []
    variants = [title]
    base = title
    suffixes = (
        r"\s*(?:[-:–—]\s*)?(?:part|cour)\s*\d+\s*$",
        r"\s*(?:[-:–—]\s*)?(?:season\s*\d+|\d+(?:st|nd|rd|th)\s+season)\s*$",
    )
    while True:
        reduced = base
        for pattern in suffixes:
            candidate = re.sub(pattern, "", base, flags=re.IGNORECASE).strip()
            if candidate != base:
                reduced = candidate
                break
        if not reduced or reduced == base:
            break
        base = reduced
        if base not in variants:
            variants.append(base)
    return variants


def _year(value):
    try:
        return int(str(value or "")[:4])
    except (TypeError, ValueError):
        return None


def _utc_date(timestamp):
    if timestamp is None:
        return None
    try:
        return datetime.datetime.fromtimestamp(
            int(timestamp), tz=datetime.timezone.utc
        ).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


class TMDBMetadataClient:
    BASE_URL = "https://api.themoviedb.org/3"

    def __init__(self, auth_type, credential, timeout=20, opener=None):
        self.auth_type = str(auth_type or "").strip().lower()
        self.credential = str(credential or "").strip()
        self.timeout = int(timeout)
        self._open = opener or urlopen
        if self.auth_type not in ("bearer", "api_key") or not self.credential:
            raise MetadataProviderError("TMDB credentials are incomplete")

    def _get(self, path, params=None):
        params = dict(params or {})
        headers = {
            "Accept": "application/json",
            "User-Agent": "Otaku-Prime/0.1.2",
        }
        if self.auth_type == "bearer":
            headers["Authorization"] = "Bearer " + self.credential
        else:
            params["api_key"] = self.credential
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = self.BASE_URL + path + (("?" + query) if query else "")
        request = Request(url, method="GET", headers=headers)
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise MetadataProviderError("TMDB rejected the supplied credential")
            raise MetadataProviderError("TMDB request failed with HTTP {}".format(exc.code))
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise MetadataProviderError("TMDB request failed: {}".format(exc))
        if isinstance(payload, dict) and payload.get("success") is False:
            raise MetadataProviderError(payload.get("status_message") or "TMDB request failed")
        return payload

    def test_connection(self):
        self._get("/configuration")
        return {"provider": "tmdb", "ok": True}

    def search_series(self, title, year=None):
        params = {"query": title, "include_adult": "false", "language": "en-US"}
        if year:
            params["first_air_date_year"] = int(year)
        payload = self._get("/search/tv", params)
        results = []
        for item in payload.get("results") or []:
            first_air = item.get("first_air_date") or ""
            results.append({
                "id": item.get("id"),
                "name": item.get("name") or item.get("original_name"),
                "original_name": item.get("original_name"),
                "year": _year(first_air),
            })
        if not results and year:
            payload = self._get("/search/tv", {
                "query": title, "include_adult": "false", "language": "en-US"
            })
            for item in payload.get("results") or []:
                results.append({
                    "id": item.get("id"),
                    "name": item.get("name") or item.get("original_name"),
                    "original_name": item.get("original_name"),
                    "year": _year(item.get("first_air_date")),
                })
        return [item for item in results if item.get("id") is not None]

    def get_show(self, show_id):
        payload = self._get("/tv/{}".format(int(show_id)), {"language": "en-US"})
        seasons = []
        for item in payload.get("seasons") or []:
            if item.get("season_number") is None:
                continue
            seasons.append({
                "id": item.get("id"),
                "number": int(item["season_number"]),
                "name": item.get("name"),
                "air_date": item.get("air_date"),
                "episode_count": item.get("episode_count"),
            })
        return {
            "id": payload.get("id"),
            "name": payload.get("name") or payload.get("original_name"),
            "original_name": payload.get("original_name"),
            "year": _year(payload.get("first_air_date")),
            "seasons": seasons,
        }

    def get_season(self, show_id, season_number, season_id=None):
        payload = self._get(
            "/tv/{}/season/{}".format(int(show_id), int(season_number)),
            {"language": "en-US"},
        )
        episodes = []
        for item in payload.get("episodes") or []:
            if item.get("episode_number") is None:
                continue
            episodes.append({
                "id": item.get("id"),
                "number": int(item["episode_number"]),
                "name": item.get("name"),
                "air_date": item.get("air_date"),
            })
        return {
            "id": payload.get("id") if payload.get("id") is not None else season_id,
            "number": int(payload.get("season_number", season_number)),
            "name": payload.get("name"),
            "air_date": payload.get("air_date"),
            "episodes": episodes,
        }


class TVDBMetadataClient:
    BASE_URL = "https://api4.thetvdb.com/v4"

    def __init__(self, api_key, pin=None, bearer_token=None, bearer_expires_at=None,
                 timeout=20, opener=None, token_callback=None):
        self.api_key = str(api_key or "").strip()
        self.pin = str(pin or "").strip() or None
        self.bearer_token = str(bearer_token or "").strip() or None
        self.bearer_expires_at = int(bearer_expires_at or 0)
        self.timeout = int(timeout)
        self._open = opener or urlopen
        self.token_callback = token_callback
        if not self.api_key:
            raise MetadataProviderError("TheTVDB API key is required")

    def _request(self, path, method="GET", params=None, body=None, auth=True):
        params = dict(params or {})
        query = urlencode({key: value for key, value in params.items() if value is not None})
        url = self.BASE_URL + path + (("?" + query) if query else "")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Otaku-Prime/0.1.2",
        }
        if auth:
            headers["Authorization"] = "Bearer " + self._token()
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, method=method, headers=headers)
        try:
            with self._open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise MetadataProviderError("TheTVDB rejected the supplied API key/PIN")
            raise MetadataProviderError("TheTVDB request failed with HTTP {}".format(exc.code))
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise MetadataProviderError("TheTVDB request failed: {}".format(exc))

    def login(self):
        body = {"apikey": self.api_key}
        if self.pin:
            body["pin"] = self.pin
        payload = self._request("/login", method="POST", body=body, auth=False)
        data = payload.get("data") or {}
        token = data.get("token") or payload.get("token")
        if not token:
            raise MetadataProviderError(
                (payload.get("message") if isinstance(payload, dict) else None)
                or "TheTVDB did not return an authentication token"
            )
        self.bearer_token = str(token)
        self.bearer_expires_at = int(time.time()) + 28 * 24 * 60 * 60
        if self.token_callback:
            self.token_callback(self.bearer_token, self.bearer_expires_at)
        return self.bearer_token

    def _token(self):
        if self.bearer_token and self.bearer_expires_at > int(time.time()) + 300:
            return self.bearer_token
        return self.login()

    def test_connection(self):
        self.login()
        return {"provider": "thetvdb", "ok": True}

    def search_series(self, title, year=None):
        payload = self._request("/search", params={
            "query": title,
            "type": "series",
            "limit": 20,
        })
        results = []
        for item in payload.get("data") or []:
            identifier = item.get("tvdb_id")
            if identifier in (None, ""):
                identifier = item.get("id")
                if isinstance(identifier, str) and identifier.startswith("series-"):
                    identifier = identifier.split("-", 1)[1]
            if identifier in (None, ""):
                continue
            result_year = _year(
                item.get("first_air_time")
                or item.get("year")
                or item.get("firstAired")
            )
            results.append({
                "id": identifier,
                "name": item.get("name") or item.get("seriesName"),
                "original_name": item.get("name") or item.get("seriesName"),
                "aliases": self._aliases(item),
                "year": result_year,
            })
        return results

    @staticmethod
    def _aliases(item):
        values=[]
        # Overviews are descriptions, not searchable titles. Including them can
        # create accidental substring matches against unrelated shows.
        for key in ("aliases","translations"):
            raw=item.get(key) or []
            if isinstance(raw,dict): raw=list(raw.values())
            if not isinstance(raw,list): raw=[raw]
            for value in raw:
                if isinstance(value,dict):
                    value=value.get("name") or value.get("title") or value.get("value")
                if value and str(value) not in values: values.append(str(value))
        return values

    def get_show(self, show_id):
        payload = self._request("/series/{}/extended".format(show_id))
        data = payload.get("data") or {}
        seasons = []
        for item in data.get("seasons") or []:
            number = item.get("number")
            if number is None:
                number = item.get("seasonNumber")
            if number is None:
                continue
            type_value = item.get("type") or {}
            type_name = (
                type_value.get("name") if isinstance(type_value, dict) else str(type_value)
            )
            if type_name and "aired" not in type_name.lower() and "default" not in type_name.lower():
                continue
            seasons.append({
                "id": item.get("id"),
                "number": int(number),
                "name": item.get("name"),
                "air_date": item.get("firstAired") or item.get("first_aired"),
            })
        return {
            "id": data.get("id") or show_id,
            "name": data.get("name") or data.get("seriesName"),
            "original_name": data.get("name") or data.get("seriesName"),
            "year": _year(data.get("firstAired") or data.get("first_air_time")),
            "seasons": seasons,
        }

    def _default_episodes(self, show_id):
        episodes = []
        page = 0
        for _ in range(20):
            payload = self._request(
                "/series/{}/episodes/default".format(show_id),
                params={"page": page},
            )
            data = payload.get("data") or {}
            rows = data.get("episodes") if isinstance(data, dict) else data
            rows = rows or []
            for item in rows:
                number = item.get("number")
                season_number = item.get("seasonNumber")
                if number is None or season_number is None:
                    continue
                episodes.append({
                    "id": item.get("id"),
                    "number": int(number),
                    "season_number": int(season_number),
                    "name": item.get("name"),
                    "air_date": item.get("aired") or item.get("firstAired"),
                })
            links = payload.get("links") or (
                data.get("links") if isinstance(data, dict) else {}
            ) or {}
            if not rows or not links.get("next"):
                break
            page += 1
        return episodes

    def get_season(self, show_id, season_number, season_id=None):
        show = self.get_show(show_id)
        season = next(
            (item for item in show.get("seasons") or []
             if int(item["number"]) == int(season_number)),
            None,
        ) or {
            "id": season_id,
            "number": int(season_number),
            "name": None,
            "air_date": None,
        }
        episodes = [
            {
                "id": item.get("id"),
                "number": item["number"],
                "name": item.get("name"),
                "air_date": item.get("air_date"),
            }
            for item in self._default_episodes(show_id)
            if int(item["season_number"]) == int(season_number)
        ]
        result = dict(season)
        result["episodes"] = episodes
        return result


class MetadataResolverService:
    """Switchable TMDB/TheTVDB authority used before Prime publishes to Kodi."""

    SPECIAL_CATEGORIES = ("movie", "ova", "oad", "special", "spin_off")
    SPECIAL_RELATIONS = ("PARENT", "SIDE_STORY", "SPIN_OFF")

    def _is_special(self, season):
        category=season.get("media_category")
        return (category in self.SPECIAL_CATEGORIES or
                (category == "ona" and season.get("relation_type") in self.SPECIAL_RELATIONS))

    def __init__(self, config_store, timeout=20, client_factory=None,
                 scraper_checker=None, scraper_installer=None):
        self.config_store = config_store
        self.timeout = int(timeout)
        self.client_factory = client_factory
        self.scraper_checker = scraper_checker
        self.scraper_installer = scraper_installer
        self._show_cache = {}
        self._stop_event = None

    def bind_stop_event(self, stop_event):
        self._stop_event = stop_event

    def status(self):
        return self.config_store.status()

    def is_configured(self):
        return self.config_store.is_ready()

    def kodi_scraper_addon(self):
        return self.status().get("kodi_scraper_addon")

    def ensure_kodi_scraper(self):
        addon_id = self.kodi_scraper_addon()
        if not addon_id:
            return {"required": None, "installed": False, "requested": False}
        installed = bool(self.scraper_checker(addon_id)) if self.scraper_checker else None
        requested = False
        if installed is False and self.scraper_installer:
            requested = bool(self.scraper_installer(addon_id))
        return {"required": addon_id, "installed": installed, "requested": requested}

    def configure(self, provider, **values):
        provider = str(provider or "").strip().lower()
        if provider not in SUPPORTED_PROVIDERS:
            raise MetadataProviderError("Choose TMDB or TheTVDB")
        previous = self.config_store.credentials() or {}
        if provider == "tmdb":
            auth_type = str(values.get("auth_type") or previous.get("auth_type") or "bearer")
            credential = str(values.get("credential") or "").strip()
            if not credential and previous.get("provider") == "tmdb":
                credential = (
                    previous.get("access_token")
                    if auth_type == "bearer"
                    else previous.get("api_key")
                ) or ""
            client = TMDBMetadataClient(
                auth_type, credential, timeout=self.timeout,
                opener=values.get("opener"),
            )
            client.test_connection()
            changed = (
                previous.get("provider") != "tmdb"
                or previous.get("auth_type") != auth_type
                or (
                    previous.get("access_token") if auth_type == "bearer"
                    else previous.get("api_key")
                ) != credential
            )
            self.config_store.save_tmdb(auth_type, credential)
        else:
            api_key = str(values.get("api_key") or "").strip()
            pin = str(values.get("pin") or "").strip()
            if previous.get("provider") == "thetvdb":
                api_key = api_key or previous.get("api_key") or ""
                if not pin:
                    pin = previous.get("pin") or ""
            client = TVDBMetadataClient(
                api_key, pin,
                timeout=self.timeout,
                opener=values.get("opener"),
            )
            token = client.login()
            changed = (
                previous.get("provider") != "thetvdb"
                or previous.get("api_key") != api_key
                or (previous.get("pin") or "") != pin
            )
            self.config_store.save_tvdb(
                api_key, pin,
                bearer_token=token,
                bearer_expires_at=client.bearer_expires_at,
            )
        if changed:
            self.config_store.invalidate_mappings()
            LOGGER.warning("Metadata authority changed to %s; cached mappings were invalidated",provider)
        else:
            LOGGER.info("Metadata authority %s credentials verified",provider)
        self._show_cache.clear()
        return self.status()

    def _client(self):
        config = self.config_store.credentials()
        if not config or not config.get("verified_at"):
            raise MetadataProviderError(
                "Configure TMDB or TheTVDB before watchlist synchronization"
            )
        provider = config["provider"]
        if self.client_factory:
            return self.client_factory(provider, config)
        if provider == "tmdb":
            auth_type = config.get("auth_type")
            credential = (
                config.get("access_token")
                if auth_type == "bearer"
                else config.get("api_key")
            )
            return TMDBMetadataClient(auth_type, credential, timeout=self.timeout)
        return TVDBMetadataClient(
            config.get("api_key"),
            config.get("pin"),
            bearer_token=config.get("bearer_token"),
            bearer_expires_at=config.get("bearer_expires_at"),
            timeout=self.timeout,
            token_callback=self.config_store.cache_tvdb_token,
        )

    def run_once(self):
        status = self.status()
        if not status.get("configured"):
            LOGGER.warning("Metadata resolution skipped: provider is not configured")
            return {
                "configured": False,
                "provider": None,
                "resolved": 0,
                "unresolved": 0,
                "failed": [],
            }
        provider = status["provider"]
        self.config_store.prepare_for_provider(provider)
        client = self._client()
        results = {"configured": True, "provider": provider,
                   "resolved": 0, "unresolved": 0, "failed": []}
        if self._stop_event is not None and self._stop_event.is_set():
            LOGGER.warning("Metadata resolution cancelled before processing started")
            results["cancelled"] = True
            return results
        for season in self.config_store.list_resolution_targets():
            if self._stop_event is not None and self._stop_event.is_set():
                LOGGER.warning(
                    "Metadata resolution cancelled after %s resolved and %s unresolved entries",
                    results["resolved"], results["unresolved"],
                )
                results["cancelled"] = True
                break
            try:
                resolved = self._resolve_target(client, provider, season)
            except Exception as exc:
                LOGGER.exception("Metadata resolution failed for AniList ID %s",season.get("anilist_id"))
                results["unresolved"] += 1
                results["failed"].append({
                    "season_id": season["local_id"],
                    "anilist_id": season.get("anilist_id"),
                    "error": str(exc),
                })
                continue
            results["resolved" if resolved else "unresolved"] += 1
        LOGGER.info("Metadata resolution complete: provider=%s resolved=%s unresolved=%s",
          provider,results["resolved"],results["unresolved"])
        if results["unresolved"]:
            LOGGER.warning("Metadata resolution left %s entries unresolved",results["unresolved"])
        return results

    def _resolve_target(self, client, provider, season):
        show = self._resolve_show(client, season)
        provider_season = self._resolve_season(client, show, season)
        local_episodes = self.config_store.list_season_episodes(season["local_id"])
        mappings, complete = self._resolve_episodes(
            season, local_episodes, provider_season.get("episodes") or []
        )
        is_special = self._is_special(season)
        resolved = bool(provider_season) and complete and (bool(local_episodes) or not is_special)
        self.config_store.apply_resolution(
            season, provider, show, provider_season, mappings, resolved
        )
        return resolved

    def _resolve_show(self, client, season):
        series_id = season["related_series_id"]
        cached = self._show_cache.get(series_id)
        if cached:
            return cached
        if (
            season.get("series_metadata_provider") == self.status().get("provider")
            and season.get("metadata_show_id")
        ):
            show = client.get_show(season["metadata_show_id"])
            self._show_cache[series_id] = show
            return show

        source_names = [
            season.get("franchise_english_name"),
            season.get("franchise_romaji_name"),
            season.get("english_name"),
            season.get("romaji_name"),
        ]
        names = []
        for source_name in source_names:
            for name in _title_variants(source_name):
                if name not in names:
                    names.append(name)
        if not names:
            raise MetadataProviderError("Franchise has no title to search")
        target_year = _year(season.get("franchise_release_date"))
        candidates_by_id = {}
        for name in names:
            for item in client.search_series(name, target_year):
                candidates_by_id[str(item["id"])]=item
        candidates=list(candidates_by_id.values())
        candidate = self._best_show(names, target_year, candidates)
        if not candidate:
            raise MetadataProviderError(
                "No confident metadata-provider series match for: {} ({} candidates)".format(
                    ", ".join(names), len(candidates)
                )
            )
        show = client.get_show(candidate["id"])
        self._show_cache[series_id] = show
        return show

    @staticmethod
    def _best_show(names, target_year, candidates):
        normalized = [_normalize(name) for name in names if name]
        scored = []
        for candidate in candidates:
            candidate_names = [
                _normalize(candidate.get("name")),
                _normalize(candidate.get("original_name")),
            ]
            candidate_names.extend(
                _normalize(value) for value in candidate.get("aliases") or []
            )
            score = 0
            for wanted in normalized:
                for actual in candidate_names:
                    if not wanted or not actual:
                        continue
                    if wanted == actual:
                        score = max(score, 100)
                    elif wanted in actual or actual in wanted:
                        score = max(score, 65)
            candidate_year = candidate.get("year")
            if target_year and candidate_year:
                difference = abs(int(target_year) - int(candidate_year))
                if difference == 0:
                    score += 30
                elif difference == 1:
                    score += 10
                elif difference >= 4:
                    score -= 20
            if score:
                scored.append((score, candidate))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored[0][0] < 65:
            return None
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    def _resolve_season(self, client, show, season):
        special = self._is_special(season)
        expected_number = 0 if special else int(
            season.get("kodi_season_number")
            if season.get("kodi_season_number") not in (None,0)
            else season.get("season_number") or 1
        )
        candidates = show.get("seasons") or []
        if special:
            match = next(
                (item for item in candidates if int(item.get("number", -1)) == 0),
                None,
            )
        else:
            match = self._best_season(season, expected_number, candidates)
        if not match:
            raise MetadataProviderError(
                "Provider show has no matching {}season".format(
                    "specials " if special else ""
                )
            )
        return client.get_season(show["id"], int(match["number"]), match.get("id"))

    @staticmethod
    def _best_season(season, expected_number, candidates):
        target_names = [
            _normalize(season.get("english_name")),
            _normalize(season.get("romaji_name")),
        ]
        release_year = _year(season.get("release_date"))
        scored = []
        for item in candidates:
            number = item.get("number")
            if number is None or int(number) == 0:
                continue
            score = 80 if int(number) == int(expected_number) else 0
            item_year = _year(item.get("air_date"))
            if release_year and item_year:
                difference = abs(release_year - item_year)
                if difference == 0:
                    score += 30
                elif difference == 1:
                    score += 12
                elif difference >= 4:
                    score -= 15
            item_name = _normalize(item.get("name"))
            for wanted in target_names:
                if not wanted or not item_name:
                    continue
                if wanted == item_name:
                    score += 45
                    break
                if wanted in item_name or item_name in wanted:
                    score += 18
                    break
            if score:
                scored.append((score, item))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]

    def _resolve_episodes(self, season, local_episodes, provider_episodes):
        special = self._is_special(season)
        used = set()
        mappings = []
        for local in local_episodes:
            match = self._match_episode(
                season, local, provider_episodes, used, special
            )
            if not match:
                return mappings, False
            used.add(str(match.get("id")))
            mappings.append({
                "local_id": local["local_id"],
                "provider_episode_id": match["id"],
                "provider_episode_number": match["number"],
                "provider_episode_name": match.get("name"),
            })
        return mappings, True

    @staticmethod
    def _match_episode(season, local, provider_episodes, used, special):
        available = [
            item for item in provider_episodes
            if item.get("id") is not None and str(item.get("id")) not in used
        ]
        source_date = _utc_date(local.get("releases_at"))
        if source_date:
            dated = [
                item for item in available
                if str(item.get("air_date") or "")[:10] == source_date
            ]
            if len(dated) == 1:
                return dated[0]

        if not special:
            numbered = [
                item for item in available
                if int(item.get("number", -1)) == int(local["episode_number"])
            ]
            if len(numbered) == 1:
                return numbered[0]
            return None

        # S00 numbering is provider-owned. Never assume AniList OVA episode 1 == S00E01.
        if len(available) == 1 and len(provider_episodes) == 1:
            return available[0]

        # A one-episode AniList special can be matched by its release date/title.
        if int(local.get("episode_number") or 0) == 1:
            release_date = str(season.get("release_date") or "")[:10]
            if release_date:
                dated = [
                    item for item in available
                    if str(item.get("air_date") or "")[:10] == release_date
                ]
                if len(dated) == 1:
                    return dated[0]
            season_names = [
                _normalize(season.get("english_name")),
                _normalize(season.get("romaji_name")),
            ]
            titled = []
            for item in available:
                item_name = _normalize(item.get("name"))
                if item_name and any(
                    wanted and (wanted == item_name or wanted in item_name or item_name in wanted)
                    for wanted in season_names
                ):
                    titled.append(item)
            if len(titled) == 1:
                return titled[0]
        return None
