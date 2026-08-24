# -*- coding: utf-8 -*-
"""Populate Prime's minimal release queue from AniList airing data."""

from __future__ import annotations

import datetime
import json
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER


class AniListReleaseScheduleService:
    API_URL = "https://graphql.anilist.co"

    def __init__(self, media_store, timeout=20, refresh_seconds=21600, opener=None):
        self.media_store = media_store
        self.timeout = int(timeout)
        self.refresh_seconds = int(refresh_seconds)
        self._rate_limited = opener is None
        self._open = opener or urlopen

    def refresh_pending(self, now=None):
        now = int(time.time() if now is None else now)
        checked_before = now - self.refresh_seconds
        result = {"checked": 0, "scheduled": 0, "failed": []}
        for season in self.media_store.list_seasons_needing_release_check(checked_before):
            try:
                scheduled = self._refresh_season(season)
            except Exception as exc:
                result["failed"].append(
                    {"season_id": season["local_id"], "error": str(exc)}
                )
                continue
            self.media_store.mark_release_schedule_checked(season["local_id"], now)
            result["checked"] += 1
            result["scheduled"] += scheduled
        return result

    def _graphql(self, anilist_id, page):
        query = """
        query($id:Int!,$page:Int!){
          Media(id:$id,type:ANIME){
            startDate{year month day}
            airingSchedule(page:$page,perPage:50){
              pageInfo{hasNextPage}
              nodes{episode airingAt}
            }
          }
        }"""
        body = json.dumps(
            {"query": query, "variables": {"id": int(anilist_id), "page": page}}
        ).encode("utf-8")
        request = Request(
            self.API_URL,
            data=body,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "Otaku-Prime/0.1.2"},
            method="POST",
        )
        for attempt in range(2):
            try:
                if self._rate_limited: ANILIST_RATE_LIMITER.wait()
                with self._open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code != 429 or attempt: raise
                time.sleep(ANILIST_RATE_LIMITER.retry_delay(exc))
        if payload.get("errors"):
            raise RuntimeError("AniList schedule request failed")
        return payload["data"]["Media"]

    def _refresh_season(self, season):
        page = 1
        nodes = []
        media = None
        while True:
            media = self._graphql(season["anilist_id"], page)
            schedule = (media or {}).get("airingSchedule") or {}
            nodes.extend(schedule.get("nodes") or [])
            if not (schedule.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1

        timestamps = [int(node["airingAt"]) for node in nodes if node.get("airingAt")]
        season_release = self._start_timestamp((media or {}).get("startDate"))
        if season_release is None and timestamps:
            season_release = min(timestamps)
        if season_release is not None:
            self.media_store.schedule_release("season", season["local_id"], season_release)

        scheduled = 0
        for node in nodes:
            if not node.get("episode") or not node.get("airingAt"):
                continue
            episode_id = self.media_store.upsert_episode(
                season["local_id"], int(node["episode"])
            )
            self.media_store.schedule_release("episode", episode_id, int(node["airingAt"]))
            scheduled += 1
        return scheduled

    @staticmethod
    def _start_timestamp(value):
        value = value or {}
        if not all(value.get(part) for part in ("year", "month", "day")):
            return None
        return int(datetime.datetime(
            int(value["year"]), int(value["month"]), int(value["day"]),
            tzinfo=datetime.timezone.utc,
        ).timestamp())
