# -*- coding: utf-8 -*-
"""Read connected tracker libraries into the canonical watchlist_items table."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from resources.lib.watchlist.mal import MAL_API_URL
from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID, SIMKL_API_URL


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


class _JsonClient:
    def __init__(self, timeout=30, opener=None):
        self.timeout = int(timeout)
        self._open = opener or urlopen

    @staticmethod
    def _json(response):
        return json.loads(response.read().decode("utf-8"))

    def _request(self, url, headers):
        request = Request(url, headers=headers)
        try:
            with self._open(request, timeout=self.timeout) as response:
                return self._json(response)
        except HTTPError as exc:
            if exc.code == 401:
                raise RuntimeError("watchlist provider rejected the access token") from exc
            raise RuntimeError(
                "watchlist provider request failed with HTTP {}".format(exc.code)
            ) from exc
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
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
        while url:
            payload = self._request(url, headers)
            rows.extend(payload.get("data") or [])
            url = ((payload.get("paging") or {}).get("next") or "").strip() or None
        return rows


class MALWatchlistImportService:
    provider = "mal"
    allow_periodic = True

    def __init__(self, accounts, watchlist_store, client=None, user_id=1):
        self.accounts = accounts
        self.store = watchlist_store
        self.client = client or MALWatchlistClient()
        self.user_id = user_id

    def sync(self):
        account = self.accounts.get_credentials(self.user_id, self.provider)
        if not account:
            self.store.replace_provider_snapshot(self.provider, [])
            return {"provider": self.provider, "connected": False, "imported": 0}
        rows = self.client.fetch(account["access_token"])
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
                "english_name": titles.get("en") or node.get("title"),
                "romaji_name": node.get("title"),
                "native_name": titles.get("ja"),
                "list_status": status,
                "progress": int(list_state.get("num_episodes_watched") or 0),
                "episode_count": node.get("num_episodes"),
                "media_format": _format(node.get("media_type")),
                "release_date": node.get("start_date"),
                "is_adult": str(node.get("nsfw") or "").lower() == "black",
                "raw": row,
            })
        self.store.replace_provider_snapshot(self.provider, normalized)
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
            "include": "anime",
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
        while url:
            payload = self._request(url, headers)
            entries.extend(payload.get("data") or [])
            for included in payload.get("included") or []:
                if included.get("type") == "anime" and included.get("id") is not None:
                    anime[str(included["id"])] = included
            url = ((payload.get("links") or {}).get("next") or "").strip() or None
        return entries, anime


class KitsuWatchlistImportService:
    provider = "kitsu"
    allow_periodic = True

    def __init__(self, accounts, watchlist_store, client=None, user_id=1):
        self.accounts = accounts
        self.store = watchlist_store
        self.client = client or KitsuWatchlistClient()
        self.user_id = user_id

    def sync(self):
        account = self.accounts.get_credentials(self.user_id, self.provider)
        if not account:
            self.store.replace_provider_snapshot(self.provider, [])
            return {"provider": self.provider, "connected": False, "imported": 0}
        entries, anime_by_id = self.client.fetch(
            account["external_user_id"], account["access_token"]
        )
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
            normalized.append({
                "provider_item_id": str(anime["id"]),
                "english_name": titles.get("en") or attrs.get("canonicalTitle"),
                "romaji_name": titles.get("en_jp") or attrs.get("canonicalTitle"),
                "native_name": titles.get("ja_jp"),
                "list_status": status,
                "progress": int(entry_attrs.get("progress") or 0),
                "episode_count": attrs.get("episodeCount"),
                "media_format": _format(attrs.get("subtype")),
                "release_date": attrs.get("startDate"),
                "is_adult": str(attrs.get("ageRating") or "").upper() == "R18",
                "raw": {"library_entry": entry, "anime": anime},
            })
        self.store.replace_provider_snapshot(self.provider, normalized)
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
        url = SIMKL_API_URL + "/sync/all-items/anime/all?" + urlencode(params)
        payload = self._request(url, self._headers(access_token))
        return payload.get("anime") or []


class SimklWatchlistImportService:
    # Simkl explicitly forbids unconditional background polling. WatchlistSync
    # skips this importer on its periodic timer; startup/manual runs use
    # activities + delta after the initial baseline.
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
            show = row.get("show") or {}
            ids = show.get("ids") or {}
            status = _status(row.get("status"))
            if not status or ids.get("simkl") is None:
                continue
            normalized.append({
                "provider_item_id": str(ids["simkl"]),
                "english_name": show.get("title"),
                "romaji_name": show.get("title"),
                "native_name": None,
                "list_status": status,
                "progress": int(row.get("watched_episodes_count") or 0),
                "episode_count": row.get("total_episodes_count"),
                "media_format": _format(row.get("anime_type")),
                # Simkl summary data gives a year but not an exact first-air date;
                # do not manufacture January 1 and accidentally bias placement.
                "release_date": None,
                "is_adult": False,
                "raw": row,
            })
        return normalized

    def _merge_delta(self, normalized):
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
                "english_name": row.get("english_name"),
                "romaji_name": row.get("romaji_name"),
                "native_name": row.get("native_name"),
                "list_status": row.get("list_status"),
                "progress": row.get("progress"),
                "episode_count": row.get("episode_count"),
                "media_format": row.get("media_format"),
                "release_date": row.get("release_date"),
                "is_adult": bool(row.get("is_adult")),
                "raw": raw,
            })
        self.store.replace_provider_snapshot(self.provider, merged)
        return len(normalized)

    def _reconcile_ids(self, current_ids):
        current_ids = {str(value) for value in current_ids}
        with self._connection() as db:
            rows = db.execute(
                "SELECT provider_item_id FROM watchlist_items WHERE provider='simkl'"
            ).fetchall()
            for row in rows:
                if str(row[0]) not in current_ids:
                    db.execute(
                        "DELETE FROM watchlist_items "
                        "WHERE provider='simkl' AND provider_item_id=?",
                        (str(row[0]),),
                    )

    def sync(self):
        account = self.accounts.get_credentials(self.user_id, self.provider)
        if not account:
            self.store.replace_provider_snapshot(self.provider, [])
            self._clear_state()
            return {"provider": self.provider, "connected": False, "imported": 0}

        token = account["access_token"]
        activities = self.client.activities(token)
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
            normalized = self._normalize(rows)
            self.store.replace_provider_snapshot(self.provider, normalized)
            self._save_state("anime_all", current or "")
            self._save_state("anime_removed", removed or "")
            return {
                "provider": self.provider,
                "connected": True,
                "imported": len(normalized),
                "mode": "initial",
            }

        if current and current == previous:
            return {
                "provider": self.provider,
                "connected": True,
                "imported": 0,
                "mode": "unchanged",
            }

        changed = self._normalize(self.client.anime(token, date_from=previous))
        count = self._merge_delta(changed)
        if removed and removed != previous_removed:
            ids = []
            for row in self.client.anime(token, ids_only=True):
                simkl_id = ((row.get("show") or {}).get("ids") or {}).get("simkl")
                if simkl_id is not None:
                    ids.append(simkl_id)
            self._reconcile_ids(ids)

        self._save_state("anime_all", current or previous)
        self._save_state("anime_removed", removed or previous_removed or "")
        return {
            "provider": self.provider,
            "connected": True,
            "imported": count,
            "mode": "delta",
        }
