# -*- coding: utf-8 -*-
"""Resolve raw tracker items to an AniList-backed franchise root."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from resources.lib.services.anilist_rate_limit import ANILIST_RATE_LIMITER
from resources.lib.services.anilist_relations import (
    AniListFranchiseResolverService,
    AniListRelationClient,
)


def _normalize(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _year(value):
    try:
        return int(str(value or "")[:4])
    except (TypeError, ValueError):
        return None


class AniListIdentityClient:
    """Translate tracker-native IDs/titles to a confident AniList anime ID."""

    API_URL = "https://graphql.anilist.co"

    def __init__(self, timeout=20, opener=None):
        self.timeout = int(timeout)
        self._open = opener or urlopen
        self._rate_limited = opener is None

    def _query(self, query, variables):
        request = Request(
            self.API_URL,
            data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Otaku-Prime/0.1.2",
            },
        )
        try:
            if self._rate_limited:
                ANILIST_RATE_LIMITER.wait()
            with self._open(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError("AniList identity lookup failed with HTTP {}".format(exc.code)) from exc
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("AniList identity lookup failed: {}".format(exc)) from exc
        if payload.get("errors"):
            raise RuntimeError("AniList identity lookup returned GraphQL errors")
        return payload.get("data") or {}

    def by_mal(self, mal_id):
        data = self._query(
            """query($idMal:Int!){Media(idMal:$idMal,type:ANIME){id}}""",
            {"idMal": int(mal_id)},
        )
        media = data.get("Media") or {}
        return str(media["id"]) if media.get("id") is not None else None

    def search(self, title):
        data = self._query(
            """query($search:String!){Page(page:1,perPage:20){media(search:$search,type:ANIME){
              id idMal format episodes startDate{year month day}
              title{english romaji native}}}}""",
            {"search": str(title)},
        )
        return ((data.get("Page") or {}).get("media") or [])

    @staticmethod
    def _best(row, candidates):
        wanted = [
            _normalize(row.get("english_name")),
            _normalize(row.get("romaji_name")),
            _normalize(row.get("native_name")),
        ]
        target_year = _year(row.get("release_date"))
        episode_count = row.get("episode_count")
        scored = []
        for candidate in candidates:
            titles = candidate.get("title") or {}
            actual = [
                _normalize(titles.get("english")),
                _normalize(titles.get("romaji")),
                _normalize(titles.get("native")),
            ]
            score = 0
            for left in wanted:
                for right in actual:
                    if not left or not right:
                        continue
                    if left == right:
                        score = max(score, 100)
                    elif left in right or right in left:
                        score = max(score, 55)
            year = (candidate.get("startDate") or {}).get("year")
            if target_year and year:
                diff = abs(int(target_year) - int(year))
                if diff == 0:
                    score += 40
                elif diff == 1:
                    score += 10
                elif diff >= 4:
                    score -= 30
            if episode_count is not None and candidate.get("episodes") is not None:
                if int(episode_count) == int(candidate["episodes"]):
                    score += 35
                elif abs(int(episode_count) - int(candidate["episodes"])) >= 6:
                    score -= 20
            if score:
                scored.append((score, candidate))
        if not scored:
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored[0][0] < 100:
            return None
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return str(scored[0][1]["id"])

    def resolve(self, row):
        provider = str(row.get("provider") or "").lower()
        if provider == "anilist":
            return str(row["provider_item_id"])
        if provider == "mal":
            return self.by_mal(row["provider_item_id"])

        # Simkl returns external IDs in its raw show stub. Prefer those instead
        # of title matching when available.
        if provider == "simkl":
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
            ids = ((raw.get("show") or {}).get("ids") or {})
            if ids.get("anilist") is not None:
                return str(ids["anilist"])
            if ids.get("mal") is not None:
                found = self.by_mal(ids["mal"])
                if found:
                    return found

        queries = []
        for value in (row.get("english_name"), row.get("romaji_name"), row.get("native_name")):
            value = str(value or "").strip()
            if value and value not in queries:
                queries.append(value)
        candidates = {}
        for query in queries:
            for candidate in self.search(query):
                if candidate.get("id") is not None:
                    candidates[str(candidate["id"])] = candidate
        return self._best(row, list(candidates.values()))


class UnifiedWatchlistFranchiseResolverService(AniListFranchiseResolverService):
    """Resolve every eligible raw tracker item to a franchise, without placing it.

    Tracker identity is translated to AniList only for relation traversal. The
    original provider/provider_item_id remains the canonical watchlist identity.
    This stage may create/update ``tv_series`` only; never seasons or episodes.
    """

    def __init__(self, media_store, watchlist_store, relation_client=None,
                 identity_client=None, max_nodes=100, preferences=None, user_id=1):
        super().__init__(
            media_store,
            client=relation_client or AniListRelationClient(),
            max_nodes=max_nodes,
            stage_only=True,
        )
        self.watchlist_store = watchlist_store
        self.watchlist_store.initialize()
        self.identity_client = identity_client or AniListIdentityClient()
        self.preferences = preferences
        self.user_id = user_id

    def _mark_root_provider(self, provider, provider_item_id):
        # save_relation stores the source provider by default; the relation root
        # in this pipeline is explicitly an AniList identity.
        with self.watchlist_store._connection() as db:
            db.execute("""UPDATE watchlist_items SET relation_root_provider='anilist'
              WHERE provider=? AND provider_item_id=?""",
              (provider, str(provider_item_id)))

    def run_once(self):
        rows = self.watchlist_store.list_relation_pending()
        if self.preferences is not None and not self.preferences.mature_content(self.user_id):
            rows = [row for row in rows if not bool(row.get("is_adult"))]
        active = []
        failed = []
        franchises = set()
        if self._stopping():
            return {"resolved": 0, "failed": [], "franchises": 0, "cancelled": True}
        if not rows:
            return {"resolved": 0, "failed": [], "franchises": 0, "staged_only": True}

        for row in rows:
            if self._stopping():
                return {
                    "resolved": len(active),
                    "failed": failed,
                    "franchises": len(franchises),
                    "cancelled": True,
                }
            provider = str(row["provider"])
            item_id = str(row["provider_item_id"])
            try:
                anilist_id = self.identity_client.resolve(row)
                if not anilist_id:
                    raise RuntimeError("No confident AniList identity for relation traversal")
                self._load_relation_graph([anilist_id])
                resolution = self._resolve(anilist_id)
                resolution["source_anilist_id"] = str(anilist_id)
                franchise_id = self.media_store.upsert_tv_series(
                    english_name=(
                        resolution.get("franchise_english_name")
                        or row.get("english_name")
                    ),
                    romaji_name=(
                        resolution.get("franchise_romaji_name")
                        or row.get("romaji_name")
                    ),
                    anilist_root_id=resolution["root_id"],
                    franchise_resolved=True,
                )
                self.watchlist_store.save_relation(
                    provider, item_id, franchise_id, resolution
                )
                self._mark_root_provider(provider, item_id)

                # Keep the legacy AniList staging mirror updated while older UI
                # and tests are being removed. Other tracker rows never touch it.
                if provider == "anilist":
                    try:
                        self.relation_store.save_resolution(item_id, franchise_id, resolution)
                    except KeyError:
                        pass

                franchises.add(franchise_id)
                active.append("{}:{}".format(provider, item_id))
            except Exception as exc:
                failed.append({
                    "provider": provider,
                    "provider_item_id": item_id,
                    "error": str(exc),
                })

        return {
            "resolved": len(active),
            "failed": failed,
            "franchises": len(franchises),
            "staged_only": True,
        }


# Transitional alias for code/tests that imported the first canonical class.
UnifiedAniListFranchiseResolverService = UnifiedWatchlistFranchiseResolverService
