# -*- coding: utf-8 -*-
"""Resolve AniList franchise roots without prematurely promoting watchlist items."""
from __future__ import annotations

import json
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from resources.lib.database.watchlist_relations import WatchlistRelationStore
from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER


SERIES_FORMATS = ("TV", "TV_SHORT", "ONA")
SPECIAL_FORMATS = ("MOVIE", "OVA", "SPECIAL", "MUSIC")
ANIME_FORMATS = SERIES_FORMATS + SPECIAL_FORMATS


class AniListRelationClient:
    API_URL = "https://graphql.anilist.co"

    def __init__(self, timeout=20, opener=None, batch_size=50):
        self.timeout = timeout
        self._rate_limited = opener is None
        self._open = opener or urlopen
        self.batch_size = max(1, min(50, int(batch_size)))

    def fetch(self, anilist_id):
        media = self.fetch_many([anilist_id])
        if not media:
            raise RuntimeError("AniList relation media was not found")
        return media[0]

    def fetch_many(self, anilist_ids):
        results = []
        values = [int(value) for value in dict.fromkeys(anilist_ids)]
        for offset in range(0, len(values), self.batch_size):
            results.extend(self._fetch_batch(values[offset:offset + self.batch_size]))
        return results

    def _fetch_batch(self, anilist_ids):
        query = """query($ids:[Int]){Page(page:1,perPage:50){media(id_in:$ids,type:ANIME){id format
          title{english romaji} startDate{year month day}
          relations{edges{relationType node{id format title{english romaji}
            startDate{year month day}}}}}}}"""
        body = json.dumps({"query": query, "variables": {"ids": anilist_ids}}).encode("utf-8")
        request = Request(
            self.API_URL,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1.2",
            },
        )
        for attempt in range(2):
            try:
                if self._rate_limited:
                    ANILIST_RATE_LIMITER.wait()
                with self._open(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as exc:
                if exc.code != 429 or attempt:
                    raise
                time.sleep(ANILIST_RATE_LIMITER.retry_delay(exc))
        media = ((payload.get("data") or {}).get("Page") or {}).get("media") or []
        if payload.get("errors"):
            raise RuntimeError("AniList relation request failed")
        return media


class AniListFranchiseResolverService:
    """Resolve watchlist rows to franchise roots before metadata placement.

    In production ``stage_only=True`` keeps the watchlist boundary separate from
    Prime's actual catalogue. The resolver may create/update the franchise row,
    but it does not create a season/episode for the watchlist item. TMDB/TVDB is
    responsible for deciding that placement in the next stage.

    ``stage_only=False`` temporarily preserves the old promotion path for older
    tests/callers while the provider-placement stage is migrated.
    """

    def __init__(self, media_store, client=None, max_nodes=100, stage_only=False,
                 legacy_relation_store=True):
        self.media_store = media_store
        self.client = client or AniListRelationClient()
        self.max_nodes = max(1, int(max_nodes))
        self.stage_only = bool(stage_only)
        self._cache = {}
        self._stop_event = None
        self.relation_store = None
        if legacy_relation_store:
            self.relation_store = WatchlistRelationStore(media_store.db_path)
            self.relation_store.initialize()

    def bind_stop_event(self, stop_event):
        self._stop_event = stop_event

    def _stopping(self):
        return self._stop_event is not None and self._stop_event.is_set()

    def run_once(self):
        staged = self.media_store.list_anilist_staging()
        active = []
        failed = []
        franchises = set()
        if self._stopping():
            return {"resolved": 0, "failed": [], "franchises": 0, "cancelled": True}
        try:
            self._load_relation_graph([entry["anilist_id"] for entry in staged])
        except Exception as exc:
            return {
                "resolved": 0,
                "failed": [{"anilist_id": None, "error": str(exc)}],
                "franchises": 0,
            }

        for entry in staged:
            if self._stopping():
                return {
                    "resolved": len(active),
                    "failed": failed,
                    "franchises": len(franchises),
                    "cancelled": True,
                }
            try:
                resolution = self._resolve(entry["anilist_id"])
                franchise_id = self.media_store.upsert_tv_series(
                    english_name=(
                        resolution.get("franchise_english_name")
                        or entry.get("english_name")
                    ),
                    romaji_name=(
                        resolution.get("franchise_romaji_name")
                        or entry.get("romaji_name")
                    ),
                    anilist_root_id=resolution["root_id"],
                    franchise_resolved=True,
                )
                if self.relation_store is not None:
                    self.relation_store.save_resolution(
                        entry["anilist_id"], franchise_id, resolution
                    )
                franchises.add(franchise_id)
                if self.stage_only:
                    active.append(str(entry["anilist_id"]))
                else:
                    active.append(
                        self.media_store.promote_anilist_season(entry, resolution)
                    )
            except Exception as exc:
                failed.append({"anilist_id": entry["anilist_id"], "error": str(exc)})

        # Provider membership belongs to promoted catalogue records, not staging
        # rows. It therefore remains a legacy-only operation until TMDB/TVDB has
        # placed the watchlist item in the real catalogue.
        if not self.stage_only and not failed:
            self.media_store.replace_provider_season_memberships("anilist", active)

        return {
            "resolved": len(active),
            "failed": failed,
            "franchises": len(franchises),
            "staged_only": self.stage_only,
        }

    def _media(self, media_id):
        key = str(media_id)
        if key not in self._cache:
            self._cache[key] = self.client.fetch(key)
        return self._cache[key]

    def _prefetch(self, media_ids):
        missing = [
            str(value)
            for value in dict.fromkeys(media_ids)
            if str(value) not in self._cache
        ]
        if not missing:
            return
        if hasattr(self.client, "fetch_many"):
            media = self.client.fetch_many(missing)
        else:
            media = [self.client.fetch(value) for value in missing]
        for item in media:
            if item and item.get("id") is not None:
                self._cache[str(item["id"])] = item

    @staticmethod
    def _date_key(media):
        date = media.get("startDate") or {}
        return (
            date.get("year") or 9999,
            date.get("month") or 99,
            date.get("day") or 99,
            int(media["id"]),
        )

    @staticmethod
    def _date_string(media):
        date = media.get("startDate") or {}
        if not date.get("year"):
            return None
        return "{:04d}-{:02d}-{:02d}".format(
            int(date["year"]),
            int(date.get("month") or 1),
            int(date.get("day") or 1),
        )

    @staticmethod
    def _edge_nodes(media, relation_types, formats=ANIME_FORMATS):
        nodes = []
        for edge in (media.get("relations") or {}).get("edges") or []:
            node = edge.get("node") or {}
            if (
                edge.get("relationType") in relation_types
                and node.get("id")
                and node.get("format") in formats
            ):
                nodes.append(node)
        return nodes

    def _backward_neighbors(self, media):
        """Return relations that can lead toward the franchise root.

        PREQUEL is always traversable, including through movies/OVAs. This is
        the critical bridge needed for chains such as TV -> MOVIE -> TV.
        PARENT/OTHER are used only by special entries to attach them to the
        owning franchise. An earlier main-series SEQUEL is a final fallback
        for AniList's reversed prequel-special relationship convention.
        """
        nodes = list(self._edge_nodes(media, ("PREQUEL",)))
        if media.get("format") in SPECIAL_FORMATS:
            nodes.extend(self._edge_nodes(media, ("PARENT",), SERIES_FORMATS))
            if not nodes:
                nodes.extend(self._edge_nodes(media, ("OTHER",), SERIES_FORMATS))
            if not nodes:
                # AniList occasionally models a prequel special as pointing to
                # the older main series with SEQUEL (for example BURN THE WITCH
                # #0.8 -> BURN THE WITCH). Only accept an earlier released
                # series so a movie cannot attach itself to a future sequel.
                nodes.extend(
                    node for node in self._edge_nodes(media, ("SEQUEL",), SERIES_FORMATS)
                    if self._date_key(node) < self._date_key(media)
                )
        deduped = {}
        for node in nodes:
            deduped[str(node["id"])] = node
        return sorted(deduped.values(), key=self._date_key)

    def _load_relation_graph(self, media_ids):
        frontier = {str(value) for value in media_ids}
        visited = set()
        limit = self.max_nodes * max(1, len(media_ids))
        while frontier and len(visited) < limit:
            if self._stopping():
                return
            self._prefetch(frontier)
            visited.update(frontier)
            following = set()
            for media_id in frontier:
                media = self._cache.get(media_id)
                if not media:
                    continue
                following.update(
                    str(node["id"]) for node in self._backward_neighbors(media)
                )
            frontier = following - visited

    def _collect_ancestors(self, media):
        queue = [media]
        seen = set()
        nodes = []
        while queue and len(seen) < self.max_nodes:
            item = queue.pop(0)
            key = str(item["id"])
            if key in seen:
                continue
            seen.add(key)
            full = self._cache.get(key) or item
            nodes.append(full)
            for neighbor in self._backward_neighbors(full):
                neighbor_id = str(neighbor["id"])
                cached = self._cache.get(neighbor_id)
                if cached is not None:
                    queue.append(cached)
        return nodes

    @staticmethod
    def _relation_type(media):
        priority = ("SPIN_OFF", "SIDE_STORY", "PARENT")
        edges = (media.get("relations") or {}).get("edges") or []
        for relation in priority:
            if any(edge.get("relationType") == relation for edge in edges):
                return relation
        return None

    def _resolve(self, media_id):
        source = self._media(media_id)
        ancestors = self._collect_ancestors(source)
        if not ancestors:
            ancestors = [source]

        main_candidates = [
            item for item in ancestors if item.get("format") in SERIES_FORMATS
        ]
        root_pool = main_candidates or ancestors
        root = sorted(root_pool, key=self._date_key)[0]

        # Keep a diagnostic relation depth for the legacy path only. It is not
        # provider season numbering and must never be treated as authoritative.
        source_date = self._date_key(source)
        numbered_before = [
            item for item in main_candidates if self._date_key(item) <= source_date
        ]
        legacy_season_number = max(1, len(numbered_before))

        root_titles = root.get("title") or {}
        original_titles = source.get("title") or {}
        original_start = source.get("startDate") or {}
        relation_type = self._relation_type(source)
        category = self._category(source.get("format"), relation_type)
        path = [
            str(item["id"])
            for item in sorted(ancestors, key=self._date_key)
        ]

        return {
            "root_id": str(root["id"]),
            "season_number": legacy_season_number,
            "franchise_english_name": (
                root_titles.get("english") or original_titles.get("english")
            ),
            "franchise_romaji_name": (
                root_titles.get("romaji") or original_titles.get("romaji")
            ),
            "franchise_release_date": self._date_string(root),
            "start_year": original_start.get("year"),
            "media_format": source.get("format"),
            "relation_type": relation_type,
            "media_category": category,
            "relation_path": path,
        }

    @staticmethod
    def _category(media_format, relation_type):
        if relation_type == "SPIN_OFF":
            return "spin_off"
        if relation_type == "SIDE_STORY":
            return "special"
        return {
            "MOVIE": "movie",
            "ONA": "ona",
            "OVA": "ova",
            "SPECIAL": "special",
            "MUSIC": "special",
        }.get(media_format, "tv")
