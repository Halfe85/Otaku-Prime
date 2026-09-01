# -*- coding: utf-8 -*-
"""Read connected tracker libraries into the canonical watchlist_items table."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from resources.lib.services.remote_identity import clean_remote_text
from resources.lib.service_lifecycle import ServiceWorkHalted
from resources.lib.logging_config import get_logger
from resources.lib.watchlist.mal import MAL_API_URL, MALAuthenticator
from resources.lib.watchlist.kitsu import KitsuAuthenticator
from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID, SIMKL_API_URL

LOGGER = get_logger(__name__)

STATUS_MAP = {
    "watching": "CURRENT",
    "current": "CURRENT",
    "completed": "COMPLETED",
    "on_hold": "PAUSED",
    "hold": "PAUSED",
    "dropped": "DROPPED",
    "plan_to_watch": "PLANNING",
    "plantowatch": "PLANNING",
    "planned": "PLANNING",
}


def _status(value):
    return STATUS_MAP.get(str(value or "").strip().lower())


def _format(value):
    value = str(value or "").strip().lower()
    return {
        "tv": "TV",
        "tv_special": "TV_SHORT",
        "tvshort": "TV_SHORT",
        "movie": "MOVIE",
        "ova": "OVA",
        "ona": "ONA",
        "special": "SPECIAL",
        "music": "MUSIC",
        "music video": "MUSIC",
    }.get(value, str(value or "").upper() or None)


class _HaltAwareImportService:
    def set_halt_event(self,event):
        self._halt_event=event

    def _checkpoint(self):
        event=getattr(self,"_halt_event",None)
        if event is not None and event.is_set():
            raise ServiceWorkHalted("watchlist import halted for addon shutdown")


class _JsonClient:
    def __init__(self, timeout=30, opener=None):
        self.timeout = int(timeout)
        self._open = opener or urlopen

    @staticmethod
    def _json(response):
        return json.loads(response.read().decode("utf-8"))

    def _request(self, url, headers):
        request = Request(url, headers=headers)
        parsed = urlsplit(url)
        endpoint = "{}://{}{}".format(parsed.scheme, parsed.netloc, parsed.path)
        service = self.__class__.__name__.replace("WatchlistClient", "") or "Watchlist"
        started = time.monotonic()
        LOGGER.info("%s API request started: GET %s", service, endpoint)
        try:
            with self._open(request, timeout=self.timeout) as response:
                payload = self._json(response)
            LOGGER.info(
                "%s API request complete: GET %s duration=%.2fs",
                service,
                endpoint,
                time.monotonic() - started,
            )
            return payload
        except HTTPError as exc:
            log = LOGGER.warning if exc.code in (401, 403, 429) else LOGGER.error
            log("%s API request failed: GET %s returned HTTP %s", service, endpoint, exc.code)
            if exc.code == 401:
                raise RuntimeError("watchlist provider rejected the access token") from exc
            raise RuntimeError(
                "watchlist provider request failed with HTTP {}".format(exc.code)
            ) from exc
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.error("%s API request failed: GET %s: %s", service, endpoint, exc)
            raise RuntimeError("watchlist provider request failed: {}".format(exc)) from exc


class MALWatchlistClient(_JsonClient):
    """MyAnimeList API v2 full anime-list reader."""

    def fetch(self, access_token):
        fields = "alternative_titles,start_date,media_type,num_episodes,nsfw,list_status"
        url = MAL_API_URL + "/users/@me/animelist?" + urlencode({
            "limit": 1000,
            "fields": fields,
        })
        headers = {
            "Authorization": "Bearer " + access_token,
            "Accept": "application/json",
            "User-Agent": "Otaku-Prime/0.1.2",
        }
        rows = []
        page = 0
        while url:
            page += 1
            payload = self._request(url, headers)
            rows.extend(payload.get("data") or [])
            url = ((payload.get("paging") or {}).get("next") or "").strip() or None
        LOGGER.info("MAL watchlist pages complete: pages=%s raw_rows=%s", page, len(rows))
        return rows


class MALWatchlistImportService(_HaltAwareImportService):
    provider = "mal"
    allow_periodic = True

    def __init__(self, accounts, watchlist_store, client=None, user_id=1, authenticator=None):
        self.accounts = accounts
        self.store = watchlist_store
        self.client = client or MALWatchlistClient()
        self.user_id = user_id
        self.authenticator=authenticator or MALAuthenticator()

    def sync(self):
        self._checkpoint()
        account = self.accounts.get_credentials(self.user_id, self.provider)
        if not account:
            self.store.replace_provider_snapshot(self.provider, [])
            LOGGER.info("MAL watchlist fetch skipped: account is not connected")
            return {"provider": self.provider, "connected": False, "imported": 0}
        LOGGER.info("MAL watchlist fetch started")
        if account.get("token_expires_at") and int(account["token_expires_at"])<=int(time.time())+60:
            access,refresh,expires=self.authenticator.refresh(account.get("refresh_token") or "")
            self._checkpoint()
            self.accounts.save(user_id=self.user_id,provider=self.provider,
              external_user_id=account["external_user_id"],external_username=account["external_username"],
              access_token=access,refresh_token=refresh,token_expires_at=expires)
            account["access_token"]=access
        rows = self.client.fetch(account["access_token"])
        self._checkpoint()
        normalized = []
        for row in rows:
            node = row.get("node") or {}
            list_state = row.get("list_status") or node.get("my_list_status") or {}
            status = _status(list_state.get("status"))
            if not status or node.get("id") is None:
                continue
            titles = node.get("alternative_titles") or {}
            normalized.append({
                "provider_item_id": str(node["id"]),
                "ids": {"mal": node["id"]},
                "english_name": titles.get("en") or node.get("title"),
                "romaji_name": node.get("title"),
                "native_name": titles.get("ja"),
                "list_status": status,
                "provider_status": list_state.get("status"),
                "progress": int(list_state.get("num_episodes_watched") or 0),
                "episode_count": node.get("num_episodes"),
                "media_format": _format(node.get("media_type")),
                "release_date": node.get("start_date"),
                "provider_updated_at": list_state.get("updated_at"),
                "is_adult": str(node.get("nsfw") or "").lower() == "black",
                "raw": row,
            })
        self._checkpoint()
        self.store.replace_provider_snapshot(self.provider, normalized)
        if not normalized:
            LOGGER.warning("MAL watchlist fetch completed with no usable anime rows")
        else:
            LOGGER.info("MAL watchlist fetch complete: imported=%s", len(normalized))
        return {
            "provider": self.provider,
            "connected": True,
            "imported": len(normalized),
        }


class KitsuWatchlistClient(_JsonClient):
    """Kitsu JSON:API user library reader with included anime records."""

    BASE_URL = "https://kitsu.io/api/edge"

    def fetch(self, user_id, access_token):
        url = self.BASE_URL + "/users/{}/library-entries?".format(user_id) + urlencode({
            "include": "anime,anime.mappings",
            "page[limit]": 500,
        })
        headers = {
            "Authorization": "Bearer " + access_token,
            "Accept": "application/vnd.api+json",
            "Content-Type": "application/vnd.api+json",
            "User-Agent": "Otaku-Prime/0.1.2",
        }
        entries = []
        anime = {}
        mappings = {}
        page = 0
        while url:
            page += 1
            payload = self._request(url, headers)
            entries.extend(payload.get("data") or [])
            for included in payload.get("included") or []:
                if included.get("type") == "anime" and included.get("id") is not None:
                    anime[str(included["id"])] = included
                elif included.get("type") == "mappings" and included.get("id") is not None:
                    mappings[str(included["id"])] = included
            url = ((payload.get("links") or {}).get("next") or "").strip() or None
        LOGGER.info("Kitsu watchlist pages complete: pages=%s raw_rows=%s", page, len(entries))
        return entries, anime, mappings


class KitsuWatchlistImportService(_HaltAwareImportService):
    provider = "kitsu"
    allow_periodic = True

    def __init__(self, accounts, watchlist_store, client=None, user_id=1, authenticator=None):
        self.accounts = accounts
        self.store = watchlist_store
        self.client = client or KitsuWatchlistClient()
        self.user_id = user_id
        self.authenticator=authenticator or KitsuAuthenticator()

    def sync(self):
        self._checkpoint()
        account = self.accounts.get_credentials(self.user_id, self.provider)
        if not account:
            self.store.replace_provider_snapshot(self.provider, [])
            LOGGER.info("Kitsu watchlist fetch skipped: account is not connected")
            return {"provider": self.provider, "connected": False, "imported": 0}
        LOGGER.info("Kitsu watchlist fetch started")
        if account.get("token_expires_at") and int(account["token_expires_at"])<=int(time.time())+60:
            access,refresh,expires=self.authenticator.refresh(account.get("refresh_token") or "")
            self._checkpoint()
            self.accounts.save(user_id=self.user_id,provider=self.provider,
              external_user_id=account["external_user_id"],external_username=account["external_username"],
              access_token=access,refresh_token=refresh,token_expires_at=expires)
            account["access_token"]=access
        entries, anime_by_id, mappings_by_id = self.client.fetch(
            account["external_user_id"], account["access_token"]
        )
        self._checkpoint()
        normalized = []
        for entry in entries:
            relationships = entry.get("relationships") or {}
            anime_ref = ((relationships.get("anime") or {}).get("data") or {})
            anime_id = anime_ref.get("id")
            anime = anime_by_id.get(str(anime_id)) if anime_id is not None else None
            if not anime:
                # Manga/library rows have no anime relationship and are ignored.
                continue
            entry_attrs = entry.get("attributes") or {}
            attrs = anime.get("attributes") or {}
            status = _status(entry_attrs.get("status"))
            if not status:
                continue
            titles = attrs.get("titles") or {}
            ids={"kitsu":anime["id"]}
            mapping_refs=(((anime.get("relationships") or {}).get("mappings") or {}).get("data") or [])
            for ref in mapping_refs:
                mapping=mappings_by_id.get(str(ref.get("id"))) or {}
                mapping_attrs=mapping.get("attributes") or {}
                site=str(mapping_attrs.get("externalSite") or "").upper()
                external_id=mapping_attrs.get("externalId")
                if external_id not in (None,""):
                    if site=="ANILIST_ANIME": ids["anilist"]=external_id
                    elif site=="MYANIMELIST_ANIME": ids["mal"]=external_id
            normalized.append({
                "provider_item_id": str(anime["id"]),
                "ids":ids,
                "english_name": titles.get("en") or attrs.get("canonicalTitle"),
                "romaji_name": titles.get("en_jp") or attrs.get("canonicalTitle"),
                "native_name": titles.get("ja_jp"),
                "list_status": status,
                "provider_status": entry_attrs.get("status"),
                "progress": int(entry_attrs.get("progress") or 0),
                "episode_count": attrs.get("episodeCount"),
                "media_format": _format(attrs.get("subtype")),
                "release_date": attrs.get("startDate"),
                "provider_updated_at": entry_attrs.get("updatedAt"),
                "is_adult": str(attrs.get("ageRating") or "").upper() == "R18",
                "raw": {"library_entry": entry, "anime": anime},
            })
        self._checkpoint()
        self.store.replace_provider_snapshot(self.provider, normalized)
        if not normalized:
            LOGGER.warning("Kitsu watchlist fetch completed with no usable anime rows")
        else:
            LOGGER.info("Kitsu watchlist fetch complete: imported=%s", len(normalized))
        return {
            "provider": self.provider,
            "connected": True,
            "imported": len(normalized),
        }


class SimklWatchlistClient(_JsonClient):
    """Simkl anime sync reader following the activities/date_from model."""

    def __init__(self, client_id=None, timeout=30, opener=None):
        super().__init__(timeout=timeout, opener=opener)
        self.client_id = str(client_id or PACKAGED_CLIENT_ID).strip()

    def _headers(self, access_token):
        return {
            "Authorization": "Bearer " + access_token,
            "Accept": "application/json",
            "User-Agent": "Otaku-Prime/0.1.2",
        }

    def activities(self, access_token):
        url = SIMKL_API_URL + "/sync/activities?" + urlencode({
            "client_id": self.client_id,
            "app-name": "otaku-prime",
            "app-version": "0.1.2",
        })
        return self._request(url, self._headers(access_token))

    def anime(self, access_token, date_from=None, ids_only=False):
        params = {
            "client_id": self.client_id,
            "app-name": "otaku-prime",
            "app-version": "0.1.2",
            "language": "en",
        }
        if date_from:
            params["date_from"] = date_from
        if ids_only:
            params["extended"] = "simkl_ids_only"
        url = SIMKL_API_URL + "/sync/all-items/anime?" + urlencode(params)
        payload = self._request(url, self._headers(access_token))
        if payload is None:
            raise RuntimeError("Simkl returned an empty anime watchlist response")
        if isinstance(payload,list):
            return payload
        if not isinstance(payload,dict):
            raise RuntimeError("Simkl returned an invalid anime watchlist response")
        return payload.get("anime") or []


class SimklWatchlistImportService(_HaltAwareImportService):
    # Periodic runs only call the cheap activities endpoint first. Full data is
    # fetched when Simkl reports a changed anime timestamp.
    provider = "simkl"
    allow_periodic = False

    def __init__(self, accounts, watchlist_store, client=None, user_id=1):
        self.accounts = accounts
        self.store = watchlist_store
        self.client = client or SimklWatchlistClient()
        self.user_id = user_id
        self._initialize_state()

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.store.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize_state(self):
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS watchlist_sync_state(
              provider TEXT NOT NULL,state_key TEXT NOT NULL,state_value TEXT,
              updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY(provider,state_key))""")

    def _state(self, key):
        with self._connection() as db:
            row = db.execute(
                "SELECT state_value FROM watchlist_sync_state "
                "WHERE provider='simkl' AND state_key=?",
                (key,),
            ).fetchone()
            return row[0] if row else None

    def _save_state(self, key, value):
        with self._connection() as db:
            db.execute("""INSERT INTO watchlist_sync_state(provider,state_key,state_value)
              VALUES('simkl',?,?) ON CONFLICT(provider,state_key) DO UPDATE SET
              state_value=excluded.state_value,updated_at=CURRENT_TIMESTAMP""", (key, value))

    def _clear_state(self):
        with self._connection() as db:
            db.execute("DELETE FROM watchlist_sync_state WHERE provider='simkl'")

    @staticmethod
    def _normalize(rows):
        normalized = []
        for row in rows:
            # The current API names this object `anime`; retain `show` support
            # for older Simkl responses already used by deployed Prime builds.
            show = row.get("anime") or row.get("show") or {}
            ids = show.get("ids") or {}
            status = _status(row.get("status"))
            if not status or ids.get("simkl") is None:
                continue
            normalized.append({
                "provider_item_id": str(ids["simkl"]),
                "ids":{name:ids.get(name) for name in ("anilist","mal","kitsu","simkl")},
                "english_name": clean_remote_text(show.get("title")),
                "romaji_name": clean_remote_text(show.get("title")),
                "native_name": None,
                "list_status": status,
                "provider_status": row.get("status"),
                "progress": int(row.get("watched_episodes_count") or 0),
                "episode_count": row.get("total_episodes_count"),
                "media_format": _format(row.get("anime_type")),
                # Simkl summary data gives a year but not an exact first-air date;
                # Preserve an unknown release date instead of manufacturing one.
                "release_date": None,
                "provider_updated_at": row.get("last_watched_at") or row.get("added_to_watchlist_at"),
                "is_adult": False,
                "raw": row,
            })
        return normalized

    def _merge_delta(self, normalized):
        self._checkpoint()
        # Preserve unchanged rows: date_from returns only changed items.
        if not normalized:
            return 0
        existing = {
            row["provider_item_id"]: row
            for row in self.store.list_provider(self.provider)
        }
        for entry in normalized:
            existing[str(entry["provider_item_id"])] = entry
        merged = []
        for item_id, row in existing.items():
            if "raw" in row:
                merged.append(row)
                continue
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            merged.append({
                "provider_item_id": item_id,
                "ids":{name:row.get(name+"_id") for name in ("anilist","mal","kitsu","simkl")},
                "english_name": row.get("english_name"),
                "romaji_name": row.get("romaji_name"),
                "native_name": row.get("native_name"),
                "list_status": row.get("list_status"),
                "provider_status":row.get("provider_status") or row.get("status"),
                "progress": row.get("progress"),
                "episode_count": row.get("episode_count"),
                "media_format": row.get("media_format"),
                "release_date": row.get("release_date"),
                "provider_updated_at":row.get("provider_updated_at"),
                "is_adult": bool(row.get("is_adult")),
                "raw": raw,
            })
        self._checkpoint()
        self.store.replace_provider_snapshot(self.provider, merged)
        return len(normalized)

    def sync(self):
        self._checkpoint()
        account = self.accounts.get_credentials(self.user_id, self.provider)
        if not account:
            self.store.replace_provider_snapshot(self.provider, [])
            self._clear_state()
            LOGGER.info("Simkl watchlist fetch skipped: account is not connected")
            return {"provider": self.provider, "connected": False, "imported": 0}

        LOGGER.info("Simkl watchlist fetch started")
        token = account["access_token"]
        activities = self.client.activities(token)
        self._checkpoint()
        anime_activity = activities.get("anime") or {}
        current = anime_activity.get("all") or activities.get("all")
        removed = anime_activity.get("removed_from_list")
        previous = self._state("anime_all")
        previous_removed = self._state("anime_removed")
        has_local_baseline = bool(self.store.list_provider(self.provider))

        # Rebuild a full baseline when this is a new connection or local raw
        # state was cleared/recreated while the timestamp survived.
        if not previous or not has_local_baseline:
            rows = self.client.anime(token)
            self._checkpoint()
            normalized = self._normalize(rows)
            self.store.replace_provider_snapshot(self.provider, normalized)
            self._save_state("anime_all", current or "")
            self._save_state("anime_removed", removed or "")
            if not normalized:
                LOGGER.warning("Simkl initial watchlist fetch completed with no usable anime rows")
            else:
                LOGGER.info("Simkl initial watchlist fetch complete: imported=%s", len(normalized))
            return {
                "provider": self.provider,
                "connected": True,
                "imported": len(normalized),
                "mode": "initial",
            }

        if current and current == previous:
            LOGGER.info("Simkl watchlist fetch complete: remote activity is unchanged")
            return {
                "provider": self.provider,
                "connected": True,
                "imported": 0,
                "mode": "unchanged",
            }

        changed = self._normalize(self.client.anime(token, date_from=previous))
        self._checkpoint()
        count = self._merge_delta(changed)
        if removed and removed != previous_removed:
            normalized=self._normalize(self.client.anime(token))
            self._checkpoint()
            self.store.replace_provider_snapshot(self.provider,normalized)
            count=len(normalized)

        self._checkpoint()
        self._save_state("anime_all", current or previous)
        self._save_state("anime_removed", removed or previous_removed or "")
        LOGGER.info("Simkl delta watchlist fetch complete: imported=%s", count)
        return {
            "provider": self.provider,
            "connected": True,
            "imported": count,
            "mode": "delta",
        }
