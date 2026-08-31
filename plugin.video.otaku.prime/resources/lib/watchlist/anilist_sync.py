# -*- coding: utf-8 -*-
"""Fetch the user's AniList snapshot into Prime's canonical watchlist boundary."""
from __future__ import annotations
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request,urlopen

from resources.lib.logging_config import get_logger
from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER

LOGGER = get_logger(__name__)
STATUSES=("CURRENT","COMPLETED","PAUSED","DROPPED","PLANNING","REPEATING")
ANILIST_HEADERS={"Content-Type":"application/json","Accept":"application/json",
                 "User-Agent":"Otaku-Prime/0.1.2"}


class AniListWatchlistClient:
    API_URL="https://graphql.anilist.co"

    def __init__(self,timeout=30,opener=None):
        self.timeout=timeout; self._rate_limited=opener is None; self._open=opener or urlopen

    def fetch(self,user_id,access_token):
        query="""query($userId:Int!){MediaListCollection(userId:$userId,type:ANIME){
          lists{status entries{status progress updatedAt media{id idMal isAdult format episodes
            startDate{year month day} synonyms title{english romaji native userPreferred}}}}}}"""
        body=json.dumps({"query":query,"variables":{"userId":int(user_id)}}).encode("utf-8")
        headers=dict(ANILIST_HEADERS); headers["Authorization"]="Bearer "+access_token
        request=Request(self.API_URL,data=body,method="POST",headers=headers)
        started = time.monotonic()
        LOGGER.info("AniList API request started: POST %s", self.API_URL)
        try:
            if self._rate_limited: ANILIST_RATE_LIMITER.wait()
            with self._open(request,timeout=self.timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            log = LOGGER.warning if exc.code in (401, 403, 429) else LOGGER.error
            log("AniList API request failed: POST %s returned HTTP %s", self.API_URL, exc.code)
            if exc.code == 401:
                raise RuntimeError("AniList authorization expired; reconnect AniList")
            if exc.code == 403:
                raise RuntimeError("AniList blocked the watchlist request (HTTP 403)")
            raise RuntimeError("AniList watchlist request failed with HTTP {}".format(exc.code))
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.error("AniList API request failed: POST %s: %s", self.API_URL, exc)
            raise RuntimeError("AniList watchlist request failed: {}".format(exc)) from exc
        if payload.get("errors"):
            LOGGER.warning(
                "AniList API request returned GraphQL errors: POST %s (%s errors)",
                self.API_URL,
                len(payload.get("errors") or []),
            )
            raise RuntimeError("AniList watchlist request failed")
        entries=[]
        for listing in payload.get("data",{}).get("MediaListCollection",{}).get("lists",[]):
            for entry in listing.get("entries") or []:
                status=entry.get("status") or listing.get("status")
                if status in STATUSES:
                    entries.append(entry)
        LOGGER.info(
            "AniList API request complete: POST %s rows=%s duration=%.2fs",
            self.API_URL,
            len(entries),
            time.monotonic() - started,
        )
        return entries


class AniListWatchlistImportService:
    def __init__(self,accounts,watchlist_store,client=None,user_id=1):
        self.accounts=accounts
        self.client=client or AniListWatchlistClient()
        self.user_id=user_id
        self.watchlist_store=watchlist_store
        self.watchlist_store.initialize()

    def sync(self):
        account=self.accounts.get_credentials(self.user_id,"anilist")
        if not account:
            self.watchlist_store.replace_provider_snapshot("anilist",[])
            LOGGER.info("AniList watchlist fetch skipped: account is not connected")
            return {"connected":False,"imported":0,"filtered":0,"watchlist_rows":0}
        LOGGER.info("AniList watchlist fetch started")
        entries=self.client.fetch(account["external_user_id"],account["access_token"])
        canonical=[]
        for entry in entries:
            media=entry.get("media") or {}
            titles=media.get("title") or {}
            synonyms=media.get("synonyms") or []
            title=(titles.get("english") or titles.get("userPreferred") or
                   titles.get("romaji") or titles.get("native") or
                   next((value for value in synonyms if value),None))
            if not media.get("id") or not title:
                continue
            provider_status=entry.get("status")
            status="CURRENT" if provider_status=="REPEATING" else provider_status
            progress=max(0,int(entry.get("progress") or 0))
            release_date=self._date(media.get("startDate"))
            is_adult=bool(media.get("isAdult"))

            # Preserve the provider snapshot exactly at the ingestion boundary.
            canonical.append({
                "provider_item_id":str(media["id"]),
                "ids":{"anilist":media["id"],"mal":media.get("idMal")},
                "english_name":titles.get("english"),
                "preferred_name":titles.get("userPreferred"),
                "romaji_name":titles.get("romaji"),
                "native_name":titles.get("native"),
                "alternative_titles":synonyms,
                "list_status":status,
                "provider_status":provider_status,
                "progress":progress,
                "episode_count":media.get("episodes"),
                "is_adult":is_adult,
                "media_format":media.get("format"),
                "release_date":release_date,
                "provider_updated_at":entry.get("updatedAt"),
                "raw":entry,
            })

        stored_count = self.watchlist_store.replace_provider_snapshot("anilist",canonical)
        if not stored_count:
            LOGGER.warning("AniList watchlist fetch completed with no usable anime rows")
        else:
            LOGGER.info("AniList watchlist fetch complete: imported=%s", stored_count)
        return {
            "connected":True,
            "imported":stored_count,
            "filtered":0,
            "watchlist_rows":stored_count,
        }

    @staticmethod
    def _date(value):
        value=value or {}
        if not value.get("year"):
            return None
        return "{:04d}-{:02d}-{:02d}".format(
          int(value["year"]),int(value.get("month") or 1),int(value.get("day") or 1))
