# -*- coding: utf-8 -*-
"""Fetch the user's AniList snapshot into Prime's canonical watchlist boundary."""
from __future__ import annotations
import json
from urllib.request import Request,urlopen
from urllib.error import HTTPError

from resources.lib.database.watchlist_items import WatchlistItemStore
from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER

STATUSES=("CURRENT","COMPLETED","PAUSED","DROPPED","PLANNING")
ANILIST_HEADERS={"Content-Type":"application/json","Accept":"application/json",
                 "User-Agent":"Otaku-Prime/0.1.2"}


class AniListWatchlistClient:
    API_URL="https://graphql.anilist.co"

    def __init__(self,timeout=30,opener=None):
        self.timeout=timeout; self._rate_limited=opener is None; self._open=opener or urlopen

    def fetch(self,user_id,access_token):
        query="""query($userId:Int!){MediaListCollection(userId:$userId,type:ANIME){
          lists{status entries{status progress media{id isAdult format episodes
            startDate{year month day} title{english romaji native}}}}}}"""
        body=json.dumps({"query":query,"variables":{"userId":int(user_id)}}).encode("utf-8")
        headers=dict(ANILIST_HEADERS); headers["Authorization"]="Bearer "+access_token
        request=Request(self.API_URL,data=body,method="POST",headers=headers)
        try:
            if self._rate_limited: ANILIST_RATE_LIMITER.wait()
            with self._open(request,timeout=self.timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 401:
                raise RuntimeError("AniList authorization expired; reconnect AniList")
            if exc.code == 403:
                raise RuntimeError("AniList blocked the watchlist request (HTTP 403)")
            raise RuntimeError("AniList watchlist request failed with HTTP {}".format(exc.code))
        if payload.get("errors"):
            raise RuntimeError("AniList watchlist request failed")
        entries=[]
        for listing in payload.get("data",{}).get("MediaListCollection",{}).get("lists",[]):
            for entry in listing.get("entries") or []:
                status=entry.get("status") or listing.get("status")
                if status in STATUSES:
                    entries.append(entry)
        return entries


class AniListWatchlistImportService:
    def __init__(self,accounts,preferences,media_store,client=None,user_id=1,
                 watchlist_store=None):
        self.accounts=accounts
        self.preferences=preferences
        self.media_store=media_store
        self.client=client or AniListWatchlistClient()
        self.user_id=user_id
        self.watchlist_store=watchlist_store or WatchlistItemStore(media_store.db_path)
        self.watchlist_store.initialize()

    def sync(self):
        account=self.accounts.get_credentials(self.user_id,"anilist")
        if not account:
            self.watchlist_store.replace_provider_snapshot("anilist",[])
            self.media_store.replace_anilist_staging([])
            return {"connected":False,"imported":0,"filtered":0,"watchlist_rows":0}
        allow_mature=self.preferences.mature_content(self.user_id)
        entries=self.client.fetch(account["external_user_id"],account["access_token"])
        filtered=0
        staged_by_id={}
        canonical=[]
        for entry in entries:
            media=entry.get("media") or {}
            titles=media.get("title") or {}
            title=titles.get("english") or titles.get("romaji") or titles.get("native")
            if not media.get("id") or not title:
                continue
            status=entry.get("status")
            progress=max(0,int(entry.get("progress") or 0))
            release_date=self._date(media.get("startDate"))
            is_adult=bool(media.get("isAdult"))

            # Canonical storage is the raw tracker snapshot. Content preferences
            # are applied by the franchise processor, not by deleting source rows.
            canonical.append({
                "provider_item_id":str(media["id"]),
                "english_name":titles.get("english"),
                "romaji_name":titles.get("romaji"),
                "native_name":titles.get("native"),
                "list_status":status,
                "progress":progress,
                "episode_count":media.get("episodes"),
                "is_adult":is_adult,
                "media_format":media.get("format"),
                "release_date":release_date,
                "raw":entry,
            })

            # Legacy staging remains filtered until its callers are removed.
            if is_adult and not allow_mature:
                filtered+=1
                continue
            staged_by_id[str(media["id"])]=dict(
                english_name=titles.get("english"),
                romaji_name=titles.get("romaji"),
                list_status=status,
                progress=progress,
                is_adult=is_adult,
                media_format=media.get("format"),
                release_date=release_date,
                anilist_id=media["id"],
            )

        self.watchlist_store.replace_provider_snapshot("anilist",canonical)
        self.media_store.replace_anilist_staging(staged_by_id.values())
        return {
            "connected":True,
            "imported":len(staged_by_id),
            "filtered":filtered,
            "watchlist_rows":len(canonical),
        }

    @staticmethod
    def _date(value):
        value=value or {}
        if not value.get("year"):
            return None
        return "{:04d}-{:02d}-{:02d}".format(
          int(value["year"]),int(value.get("month") or 1),int(value.get("day") or 1))
