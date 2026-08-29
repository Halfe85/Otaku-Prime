# -*- coding: utf-8 -*-
"""Native AniList franchise and episode placement for Prime watchlist items."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER
from resources.lib.services.mediator_helper_simkl import (
    MediatorMetadataPending,
    MediatorPlacementError,
)


MAX_PREQUEL_DEPTH = 64
SPECIAL_FORMATS = ("MOVIE", "OVA", "ONA", "SPECIAL", "MUSIC")
SEASON_FORMATS = ("TV", "TV_SHORT")


class AniListMediatorClient:
    """Small cached AniList GraphQL client used only by native mediation."""

    API_URL = "https://graphql.anilist.co"

    def __init__(self, timeout=30, opener=None):
        self.timeout = int(timeout)
        self._open = opener or urlopen
        self._rate_limited = opener is None
        self._media_cache = {}
        self._schedule_cache = {}
        self._cast_cache = {}

    def _query(self, query, variables):
        request = Request(
            self.API_URL,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1.2 anilist-mediator",
            },
        )
        payload = None
        for attempt in range(2):
            try:
                if self._rate_limited:
                    ANILIST_RATE_LIMITER.wait()
                with self._open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    time.sleep(ANILIST_RATE_LIMITER.retry_delay(exc))
                    continue
                raise MediatorPlacementError(
                    "AniList GraphQL returned HTTP {}".format(exc.code)
                ) from exc
            except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                raise MediatorPlacementError("AniList GraphQL failed: {}".format(exc)) from exc
        if not isinstance(payload, dict):
            raise MediatorPlacementError("AniList GraphQL returned an invalid response")
        if payload.get("errors"):
            messages = ", ".join(
                str(row.get("message") or "unknown GraphQL error")
                for row in payload.get("errors") or []
            )
            raise MediatorPlacementError("AniList GraphQL failed: {}".format(messages))
        return payload.get("data") or {}

    def media(self, anilist_id):
        key = str(anilist_id)
        if key not in self._media_cache:
            data = self._query(
                """query($id:Int!){Media(id:$id,type:ANIME){
                  id idMal format episodes status duration description(asHtml:false)
                  title{english romaji native}
                  startDate{year month day} endDate{year month day}
                  nextAiringEpisode{episode airingAt}
                  relations{edges{relationType(version:2) node{
                    id idMal type format episodes status duration description(asHtml:false)
                    title{english romaji native}
                    startDate{year month day} endDate{year month day}
                  }}}
                }}""",
                {"id": int(key)},
            )
            media = data.get("Media") or {}
            if not media.get("id"):
                raise MediatorPlacementError("AniList media {} was not found".format(key))
            self._media_cache[key] = media
        return self._media_cache[key]

    def schedule(self, anilist_id):
        key = str(anilist_id)
        if key in self._schedule_cache:
            return self._schedule_cache[key]
        page = 1
        rows = []
        while True:
            data = self._query(
                """query($id:Int!,$page:Int!){Media(id:$id,type:ANIME){
                  airingSchedule(page:$page,perPage:25){
                    pageInfo{hasNextPage}
                    nodes{episode airingAt}
                  }
                }}""",
                {"id": int(key), "page": page},
            )
            connection = ((data.get("Media") or {}).get("airingSchedule") or {})
            rows.extend(connection.get("nodes") or [])
            if not (connection.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1
            if page > 100:
                raise MediatorPlacementError("AniList airing schedule exceeded its safety limit")
        self._schedule_cache[key] = rows
        return rows

    def cast(self, anilist_id):
        """Return original Japanese voice actor -> character pairs for this anime."""
        key = str(anilist_id)
        if key in self._cast_cache:
            return self._cast_cache[key]
        page = 1
        result = []
        seen = set()
        while True:
            data = self._query(
                """query($id:Int!,$page:Int!){Media(id:$id,type:ANIME){
                  characters(page:$page,perPage:25){
                    pageInfo{hasNextPage}
                    edges{
                      node{name{full}}
                      voiceActors(language:JAPANESE){name{full}}
                    }
                  }
                }}""",
                {"id": int(key), "page": page},
            )
            connection = ((data.get("Media") or {}).get("characters") or {})
            for edge in connection.get("edges") or []:
                character = (((edge or {}).get("node") or {}).get("name") or {}).get("full")
                if not character:
                    continue
                for actor in (edge or {}).get("voiceActors") or []:
                    person = ((actor or {}).get("name") or {}).get("full")
                    if not person:
                        continue
                    marker = (str(person), str(character))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    result.append({
                        "person_name": str(person),
                        "character_name": str(character),
                        "sort_order": len(result),
                    })
            if not (connection.get("pageInfo") or {}).get("hasNextPage"):
                break
            page += 1
            if page > 50:
                raise MediatorPlacementError("AniList character list exceeded its safety limit")
        self._cast_cache[key] = result
        return result


def _date_key(media):
    value = media.get("startDate") or {}
    return (
        int(value.get("year") or 9999),
        int(value.get("month") or 99),
        int(value.get("day") or 99),
        int(media.get("id") or 0),
    )


def _date_string(value):
    value = value or {}
    if not value.get("year"):
        return None
    return "{:04d}-{:02d}-{:02d}".format(
        int(value["year"]), int(value.get("month") or 1), int(value.get("day") or 1)
    )


def _titles(media):
    values = media.get("title") or {}
    return {
        "english": values.get("english") or values.get("romaji") or values.get("native"),
        "romaji": values.get("romaji") or values.get("english") or values.get("native"),
    }


def _prequel_ids(media):
    result = []
    for edge in (media.get("relations") or {}).get("edges") or []:
        node = edge.get("node") or {}
        if (
            str(edge.get("relationType") or "").upper() == "PREQUEL"
            and str(node.get("type") or "ANIME").upper() == "ANIME"
            and node.get("id") not in (None, "")
        ):
            result.append(str(node["id"]))
    return list(dict.fromkeys(result))


def _find_bottom_root(client, target):
    """Return the terminal PREQUEL root and its exact root-to-target path."""
    target_id = str(target["id"])
    frontier = [(target, [target], {target_id})]
    terminal_paths = []
    visited_nodes = 0
    while frontier:
        current, path, seen = frontier.pop(0)
        visited_nodes += 1
        if visited_nodes > MAX_PREQUEL_DEPTH * MAX_PREQUEL_DEPTH:
            raise MediatorPlacementError("AniList prequel graph exceeded its safety limit")
        candidates = []
        for candidate_id in _prequel_ids(current):
            if candidate_id in seen:
                continue
            candidates.append(client.media(candidate_id))
        if not candidates:
            terminal_paths.append(path)
            continue
        if len(path) >= MAX_PREQUEL_DEPTH:
            raise MediatorPlacementError("AniList prequel path exceeded its safety limit")
        for candidate in sorted(candidates, key=_date_key):
            candidate_id = str(candidate["id"])
            frontier.append((candidate, path + [candidate], seen | {candidate_id}))
    if not terminal_paths:
        raise MediatorPlacementError("AniList prequel graph has no terminal root")
    selected = sorted(
        terminal_paths,
        key=lambda path: (-len(path), _date_key(path[-1])),
    )[0]
    return selected[-1], list(reversed(selected))


def _season_number(target, path):
    media_format = str(target.get("format") or "").upper()
    if media_format in SPECIAL_FORMATS:
        return 0, "anilist_special_format"
    numbered = [
        node for node in path if str(node.get("format") or "").upper() in SEASON_FORMATS
    ]
    target_id = str(target["id"])
    for index, node in enumerate(numbered, 1):
        if str(node["id"]) == target_id:
            return index, "anilist_prequel_position"
    return max(1, len(numbered) + 1), "anilist_prequel_position"


def _episode_count(target, item, schedule):
    values = [target.get("episodes"), item.get("episode_count")]
    scheduled = [int(row["episode"]) for row in schedule if row.get("episode") not in (None, "")]
    if scheduled:
        values.append(max(scheduled))
    next_airing = target.get("nextAiringEpisode") or {}
    if next_airing.get("episode") not in (None, ""):
        values.append(int(next_airing["episode"]))
    for value in values:
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        if count > 0:
            return count
    raise MediatorMetadataPending(
        "AniList has no episode count for the requested watchlist item")


def _special_offset(target, path):
    target_id = str(target["id"])
    offset = 0
    for node in path:
        if str(node["id"]) == target_id:
            break
        if str(node.get("format") or "").upper() not in SPECIAL_FORMATS:
            continue
        try:
            count = int(node.get("episodes") or 0)
        except (TypeError, ValueError):
            count = 0
        offset += max(0, count)
    return offset


def _release_dates(target, schedule):
    dates = {}
    for row in schedule:
        try:
            episode = int(row["episode"])
            airing_at = int(row["airingAt"])
        except (KeyError, TypeError, ValueError):
            continue
        dates[episode] = datetime.fromtimestamp(airing_at, timezone.utc).date().isoformat()
    if 1 not in dates:
        first = _date_string(target.get("startDate"))
        if first:
            dates[1] = first
    return dates


def _year(media):
    try:
        return int((media.get("startDate") or {}).get("year"))
    except (TypeError, ValueError):
        return None


def _runtime(media):
    try:
        value = int(media.get("duration"))
        return value if value > 0 else None
    except (TypeError, ValueError):
        return None


class AniListMediatorHelper:
    provider = "anilist"

    def __init__(self, client=None):
        self.client = client or AniListMediatorClient()

    def resolve(self, item, client=None):
        value = item.get("anilist_id")
        if value in (None, ""):
            raise MediatorPlacementError("watchlist item has no AniList ID")
        target = self.client.media(value)
        if str(target.get("id")) != str(value):
            raise MediatorPlacementError("AniList returned a different media identity")
        root, path = _find_bottom_root(self.client, target)
        season_number, number_source = _season_number(target, path)
        schedule = self.client.schedule(value)
        count = _episode_count(target, item, schedule)
        offset = _special_offset(target, path) if season_number == 0 else 0
        dates = _release_dates(target, schedule)
        episodes = []
        for source_number in range(1, count + 1):
            episodes.append({
                "source_episode_number": source_number,
                "episode_number": offset + source_number,
                "season_number": season_number,
                "simkl_id": None,
                "mal_id": None,
                "title": None,
                "overview": None,
                "runtime_minutes": _runtime(target),
                "release_date": dates.get(source_number),
            })
        root_titles = _titles(root)
        target_titles = _titles(target)
        numbers = [row["episode_number"] for row in episodes]
        root_format = str(root.get("format") or "").upper() or None
        return {
            "provider_path": self.provider,
            "provider_id": str(value),
            "tv_show": {
                "name": root_titles["english"] or target_titles["english"],
                "romaji_name": root_titles["romaji"] or target_titles["romaji"],
                "simkl_id": None,
                "tvdb_id": None,
                "anilist_id": str(root["id"]),
                "source_format": root_format,
                "source": "anilist_bottom_relation",
                "publish_year": _year(root) or _year(target),
                "overview": target.get("description") or root.get("description"),
                "runtime_minutes": _runtime(target) or _runtime(root),
                "air_status": target.get("status") or root.get("status"),
                "cast": self.client.cast(value),
            },
            "season": {
                "number": season_number,
                "number_source": number_source,
                "name": target_titles["english"],
                "media_type": str(target.get("format") or "").lower(),
                "first_episode": numbers[0],
                "last_episode": numbers[-1],
            },
            "episodes": episodes,
            "relation_path": [str(node["id"]) for node in path],
        }
