# -*- coding: utf-8 -*-
"""Write Prime master watchlist state back to connected tracker providers."""
from __future__ import annotations

import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from resources.lib.watchlist.mal import MAL_API_URL, MALAuthenticator
from resources.lib.watchlist.kitsu import KitsuAuthenticator
from resources.lib.watchlist.simkl import PACKAGED_CLIENT_ID, SIMKL_API_URL


ANILIST_API_URL = "https://graphql.anilist.co"
KITSU_API_URL = "https://kitsu.io/api/edge"
PROVIDER_WRITE_INTERVALS = {
    "anilist": 0.7,
    "mal": 1.1,
    "kitsu": 0.5,
    "simkl": 0.4,
}

MAL_STATUS = {
    "CURRENT": "watching",
    "COMPLETED": "completed",
    "PAUSED": "on_hold",
    "DROPPED": "dropped",
    "PLANNING": "plan_to_watch",
}
KITSU_STATUS = {
    "CURRENT": "current",
    "COMPLETED": "completed",
    "PAUSED": "on_hold",
    "DROPPED": "dropped",
    "PLANNING": "planned",
}
SIMKL_STATUS = {
    "CURRENT": "watching",
    "COMPLETED": "completed",
    "PAUSED": "hold",
    # Simkl's add-to-list write API calls the dropped bucket notinteresting.
    "DROPPED": "notinteresting",
    "PLANNING": "plantowatch",
}


class WatchlistProviderWriteError(RuntimeError):
    def __init__(self, message, status_code=None, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = status_code is None or status_code in (307, 408, 425, 429) \
            or (isinstance(status_code, int) and status_code >= 500)


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WatchlistProviderWriter:
    """Push Prime state using each tracker's native representation.

    AniList, MAL and Kitsu receive a sequential progress counter.  Simkl is
    different: its history API receives explicit episode additions/removals for
    the range changed by Prime's canonical progress boundary.
    """

    def __init__(self, accounts, user_id=1, timeout=20, opener=None,
                 mal_authenticator=None, kitsu_authenticator=None,
                 simkl_client_id=None, monotonic=None, sleeper=None):
        self.accounts = accounts
        self.user_id = int(user_id)
        self.timeout = int(timeout)
        self._open = opener or urlopen
        self.mal_authenticator = mal_authenticator or MALAuthenticator()
        self.kitsu_authenticator = kitsu_authenticator or KitsuAuthenticator()
        self.simkl_client_id = str(simkl_client_id or PACKAGED_CLIENT_ID).strip()
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleeper or time.sleep
        self._last_request_at = {}
        self._pace_lock = threading.Lock()

    def _pace(self, provider):
        interval = float(PROVIDER_WRITE_INTERVALS.get(provider, 0))
        if interval <= 0:
            return
        with self._pace_lock:
            now = self._monotonic()
            delay = self._last_request_at.get(provider, now - interval) + interval - now
            if delay > 0:
                self._sleep(delay)
                now = self._monotonic()
            self._last_request_at[provider] = now

    def _credentials(self, provider):
        account = self.accounts.get_credentials(self.user_id, provider)
        if not account:
            return None
        expires = account.get("token_expires_at")
        if provider in ("mal", "kitsu") and expires and int(expires) <= int(time.time()) + 60:
            authenticator = self.mal_authenticator if provider == "mal" else self.kitsu_authenticator
            access, refresh, new_expires = authenticator.refresh(account.get("refresh_token") or "")
            self.accounts.save(
                user_id=self.user_id,
                provider=provider,
                external_user_id=account["external_user_id"],
                external_username=account["external_username"],
                access_token=access,
                refresh_token=refresh,
                token_expires_at=new_expires,
            )
            account["access_token"] = access
            account["refresh_token"] = refresh
            account["token_expires_at"] = new_expires
        return account

    def _request(self, request, service, provider=None):
        self._pace(provider or str(service).lower())
        try:
            with self._open(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                detail = ""
            retry_after = None
            try:
                retry_after = int(exc.headers.get("Retry-After"))
            except (AttributeError, TypeError, ValueError):
                pass
            raise WatchlistProviderWriteError(
                "{} rejected watchlist update (HTTP {}{})".format(
                    service, exc.code, ": " + detail if detail else ""
                ), status_code=exc.code, retry_after=retry_after
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise WatchlistProviderWriteError(
                "Unable to update {} watchlist".format(service), retry_after=60
            ) from exc
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WatchlistProviderWriteError("{} returned invalid JSON".format(service)) from exc
        return value

    def push(self, provider, item, provider_entry=None):
        provider = str(provider or "").lower()
        account = self._credentials(provider)
        if not account:
            return {"provider": provider, "connected": False, "skipped": True}
        if provider == "anilist":
            return self._push_anilist(account, item)
        if provider == "mal":
            return self._push_mal(account, item)
        if provider == "kitsu":
            return self._push_kitsu(account, item, provider_entry)
        if provider == "simkl":
            return self._push_simkl(account, item, provider_entry)
        raise WatchlistProviderWriteError("Unsupported watchlist provider: {}".format(provider))

    @staticmethod
    def target_state(provider, item, provider_entry=None):
        """Translate Prime's canonical state to a provider-valid equivalent."""
        status = item["status"]
        progress = max(0, int(item.get("progress") or 0))
        if provider == "kitsu" and provider_entry:
            count = provider_entry.get("episode_count")
            try:
                count = int(count) if count is not None else None
            except (TypeError, ValueError):
                count = None
            if count is not None and count >= 0:
                progress = min(progress, count)
                if status == "COMPLETED":
                    progress = count
        return {"status": status, "progress": progress}

    def _push_anilist(self, account, item):
        media_id = item.get("anilist_id")
        if media_id in (None, ""):
            return {"provider": "anilist", "skipped": True, "reason": "missing_provider_id"}
        query = """mutation($mediaId:Int!,$status:MediaListStatus,$progress:Int){
          SaveMediaListEntry(mediaId:$mediaId,status:$status,progress:$progress){
            status progress updatedAt
          }}"""
        body = json.dumps({
            "query": query,
            "variables": {
                "mediaId": int(media_id),
                "status": item["status"],
                "progress": max(0, int(item.get("progress") or 0)),
            },
        }).encode("utf-8")
        request = Request(ANILIST_API_URL, data=body, method="POST", headers={
            "Authorization": "Bearer " + account["access_token"],
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Otaku-Prime/0.1.2 watchdog",
        })
        payload = self._request(request, "AniList", provider="anilist")
        if payload.get("errors"):
            raise WatchlistProviderWriteError("AniList returned GraphQL errors during watchlist update")
        row = (payload.get("data") or {}).get("SaveMediaListEntry") or {}
        return {
            "provider": "anilist", "updated": True,
            "status": row.get("status") or item["status"],
            "progress": int(row.get("progress") if row.get("progress") is not None else item.get("progress") or 0),
            "updated_at": row.get("updatedAt") or _now_iso(),
        }

    def _push_mal(self, account, item):
        media_id = item.get("mal_id")
        if media_id in (None, ""):
            return {"provider": "mal", "skipped": True, "reason": "missing_provider_id"}
        data = urlencode({
            "status": MAL_STATUS[item["status"]],
            "num_watched_episodes": max(0, int(item.get("progress") or 0)),
        }).encode("utf-8")
        request = Request(
            MAL_API_URL + "/anime/{}/my_list_status".format(media_id),
            data=data,
            method="PUT",
            headers={
                "Authorization": "Bearer " + account["access_token"],
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1.2 watchdog",
            },
        )
        row = self._request(request, "MyAnimeList", provider="mal")
        return {
            "provider": "mal", "updated": True,
            "status": item["status"],
            "progress": int(row.get("num_episodes_watched") if row.get("num_episodes_watched") is not None else item.get("progress") or 0),
            "updated_at": row.get("updated_at") or _now_iso(),
        }

    @staticmethod
    def _kitsu_entry_id(provider_entry):
        if not provider_entry:
            return None
        try:
            raw = json.loads(provider_entry.get("raw_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        library_entry = raw.get("library_entry") or raw
        value = library_entry.get("id") if isinstance(library_entry, dict) else None
        return str(value) if value not in (None, "") else None

    def _push_kitsu(self, account, item, provider_entry):
        anime_id = item.get("kitsu_id")
        if anime_id in (None, ""):
            return {"provider": "kitsu", "skipped": True, "reason": "missing_provider_id"}
        entry_id = self._kitsu_entry_id(provider_entry)
        target = self.target_state("kitsu", item, provider_entry)
        attributes = {
            "status": KITSU_STATUS[target["status"]],
            "progress": target["progress"],
        }
        if entry_id:
            body = {"data": {"type": "libraryEntries", "id": entry_id, "attributes": attributes}}
            url = KITSU_API_URL + "/library-entries/" + entry_id
            method = "PATCH"
        else:
            body = {"data": {
                "type": "libraryEntries",
                "attributes": attributes,
                "relationships": {
                    "user": {"data": {"type": "users", "id": str(account["external_user_id"])}},
                    "anime": {"data": {"type": "anime", "id": str(anime_id)}},
                },
            }}
            url = KITSU_API_URL + "/library-entries"
            method = "POST"
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            method=method,
            headers={
                "Authorization": "Bearer " + account["access_token"],
                "Content-Type": "application/vnd.api+json",
                "Accept": "application/vnd.api+json",
                "User-Agent": "Otaku-Prime/0.1.2 watchdog",
            },
        )
        payload = self._request(request, "Kitsu", provider="kitsu")
        data = payload.get("data") or {}
        attrs = data.get("attributes") or {}
        return {
            "provider": "kitsu", "updated": True,
            "provider_entry_id": data.get("id") or entry_id,
            "status": target["status"],
            "progress": int(attrs.get("progress") if attrs.get("progress") is not None else target["progress"]),
            "updated_at": attrs.get("updatedAt") or _now_iso(),
        }

    def _simkl_post(self, account, path, body):
        request = Request(
            SIMKL_API_URL + path,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + account["access_token"],
                "simkl-api-key": self.simkl_client_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1.2 watchdog",
            },
        )
        return self._request(request, "Simkl", provider="simkl")

    def _push_simkl(self, account, item, provider_entry):
        simkl_id = item.get("simkl_id")
        if simkl_id in (None, ""):
            return {"provider": "simkl", "skipped": True, "reason": "missing_provider_id"}
        old_progress = int((provider_entry or {}).get("progress") or 0)
        new_progress = max(0, int(item.get("progress") or 0))
        # Unlike the other trackers, Simkl stores episode history.  Translate
        # the changed Prime boundary to concrete episode numbers here.
        if new_progress > old_progress:
            self._simkl_post(account, "/sync/history", {
                "shows": [{
                    "ids": {"simkl": int(simkl_id)},
                    "episodes": [{"number": number} for number in range(old_progress + 1, new_progress + 1)],
                }]
            })
        elif new_progress < old_progress:
            self._simkl_post(account, "/sync/history/remove", {
                "shows": [{
                    "ids": {"simkl": int(simkl_id)},
                    "episodes": [{"number": number} for number in range(new_progress + 1, old_progress + 1)],
                }]
            })
        self._simkl_post(account, "/sync/add-to-list", {
            "shows": [{"to": SIMKL_STATUS[item["status"]], "ids": {"simkl": int(simkl_id)}}]
        })
        return {
            "provider": "simkl", "updated": True,
            "status": item["status"], "progress": new_progress,
            "updated_at": _now_iso(),
        }
