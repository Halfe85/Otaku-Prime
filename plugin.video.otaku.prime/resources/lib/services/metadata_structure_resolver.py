# -*- coding: utf-8 -*-
"""Place raw watchlist items inside the selected metadata provider's structure."""
from __future__ import annotations

import datetime

from resources.lib.database.provider_structure import ProviderStructureStore
from resources.lib.logging_config import get_logger
from resources.lib.services.metadata_resolver import (
    MetadataProviderError,
    TMDBMetadataClient,
    _normalize,
    _year,
)
from resources.lib.services.metadata_resolver_default_order import (
    MetadataResolverService as DefaultOrderResolverService,
    TVDBDefaultOrderMetadataClient,
)

LOGGER = get_logger(__name__)


def _date(value):
    try:
        return datetime.date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _days(left, right):
    left = _date(left)
    right = _date(right)
    if left is None or right is None:
        return None
    return abs((left - right).days)


class MetadataStructureResolverService(DefaultOrderResolverService):
    """Resolve franchise first, then compare one raw watchlist row to all nodes.

    There is deliberately no pre-placement ``is_special`` branch here.  A row
    becomes a normal season or a Season-0 episode only because the provider
    structure itself wins the comparison.
    """

    MIN_CONFIDENCE = 100
    MIN_MARGIN = 20

    def __init__(self, config_store, watchlist_store, timeout=20,
                 client_factory=None, scraper_checker=None, scraper_installer=None,
                 media_store=None):
        super().__init__(
            config_store,
            timeout=timeout,
            client_factory=client_factory,
            scraper_checker=scraper_checker,
            scraper_installer=scraper_installer,
            media_store=media_store,
        )
        if media_store is None:
            raise ValueError("metadata structure resolver requires media_store")
        self.watchlist_store = watchlist_store
        self.watchlist_store.initialize()
        self.structure_store = ProviderStructureStore(media_store.db_path)
        self._structure_cache = {}

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
        return TVDBDefaultOrderMetadataClient(
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
            return {
                "configured": False,
                "provider": None,
                "placed": 0,
                "unresolved": 0,
                "failed": [],
            }
        provider = status["provider"]
        self.config_store.prepare_for_provider(provider)
        preparer = getattr(self.watchlist_store, "prepare_for_metadata_provider", None)
        if preparer:
            preparer(provider)
        client = self._client()
        result = {
            "configured": True,
            "provider": provider,
            "placed": 0,
            "unresolved": 0,
            "failed": [],
        }
        targets = self.watchlist_store.list_placement_pending()
        for item in targets:
            if self._stop_event is not None and self._stop_event.is_set():
                result["cancelled"] = True
                break
            try:
                placement = self._place_watchlist_item(client, provider, item)
                result["placed"] += 1
                LOGGER.info(
                    "Metadata placement: %s:%s -> %s show=%s season=%s episode=%s score=%s",
                    item.get("provider"), item.get("provider_item_id"),
                    placement["kind"], placement["show_id"],
                    placement.get("season_number"), placement.get("episode_number"),
                    placement.get("score"),
                )
            except Exception as exc:
                LOGGER.exception(
                    "Metadata placement failed for watchlist %s:%s",
                    item.get("provider"), item.get("provider_item_id"),
                )
                result["unresolved"] += 1
                result["failed"].append({
                    "provider": item.get("provider"),
                    "provider_item_id": item.get("provider_item_id"),
                    "error": str(exc),
                })
        return result

    def _place_watchlist_item(self, client, provider, item):
        target = dict(item)
        target["related_series_id"] = item["franchise_local_id"]
        target["franchise_release_date"] = item.get("franchise_release_date")
        show = self._resolve_show(client, target)
        structure = self._provider_structure(client, provider, show)
        candidates = self._placement_candidates(item, structure)
        placement = self._choose_candidate(item, show, candidates)
        placement["metadata_provider"] = provider
        placement["show_id"] = show["id"]

        season = placement["season"]
        if placement["kind"] == "season":
            selected_episodes = list(season.get("episodes") or [])
        else:
            selected_episodes = [placement["episode"]]

        season_local_id = self.media_store.upsert_season(
            item["franchise_local_id"],
            int(season["number"]),
            english_name=season.get("name"),
            release_date=season.get("air_date"),
            kodi_show_name=show.get("name"),
            kodi_show_year=show.get("year"),
            kodi_season_number=int(season["number"]),
            kodi_resolved=True,
        )

        mappings = []
        for episode in selected_episodes:
            if episode.get("id") is None or episode.get("number") is None:
                continue
            local_id = self.media_store.upsert_episode(
                season_local_id, int(episode["number"])
            )
            mappings.append({
                "local_id": local_id,
                "provider_episode_id": episode["id"],
                "provider_episode_number": int(episode["number"]),
                "provider_episode_name": episode.get("name"),
            })

        if placement["kind"] == "season" and not mappings:
            raise MetadataProviderError("Provider season contains no episodes to place")
        if placement["kind"] == "special_episode" and len(mappings) != 1:
            raise MetadataProviderError("Provider special episode could not be materialized")

        self.structure_store.apply(
            item["franchise_local_id"], season_local_id, provider,
            show, season, mappings,
        )
        self.watchlist_store.save_placement(
            item["provider"], item["provider_item_id"], placement,
            catalogue_season_local_id=season_local_id,
        )
        return placement

    def _provider_structure(self, client, provider, show):
        cache_key = (provider, str(show["id"]))
        cached = self._structure_cache.get(cache_key)
        if cached is not None:
            return cached
        seasons = []
        for summary in show.get("seasons") or []:
            if summary.get("number") is None:
                continue
            full = client.get_season(
                show["id"], int(summary["number"]), summary.get("id")
            )
            full = dict(full)
            full.setdefault("id", summary.get("id"))
            full.setdefault("number", int(summary["number"]))
            full.setdefault("name", summary.get("name"))
            full.setdefault("air_date", summary.get("air_date"))
            full["episode_count"] = len(full.get("episodes") or [])
            seasons.append(full)
        structure = {"show": show, "seasons": seasons}
        self._structure_cache[cache_key] = structure
        LOGGER.info(
            "Provider structure: provider=%s show=%s id=%s seasons=%s",
            provider, show.get("name"), show.get("id"),
            [
                "S{}:{}eps:{}".format(
                    season.get("number"), len(season.get("episodes") or []),
                    season.get("air_date") or "?"
                )
                for season in seasons
            ],
        )
        return structure

    @classmethod
    def _placement_candidates(cls, item, structure):
        show = structure["show"]
        seasons = structure.get("seasons") or []
        normal_seasons = [s for s in seasons if int(s.get("number", -1)) != 0]
        candidates = []
        for season in normal_seasons:
            score, reasons = cls._score_season(item, show, season, len(normal_seasons))
            candidates.append({
                "kind": "season",
                "score": score,
                "reasons": reasons,
                "season": season,
                "season_id": season.get("id"),
                "season_number": int(season["number"]),
                "episode_id": None,
                "episode_number": None,
            })

        season_zero = next(
            (s for s in seasons if int(s.get("number", -1)) == 0), None
        )
        if season_zero:
            for episode in season_zero.get("episodes") or []:
                score, reasons = cls._score_special_episode(item, episode)
                candidates.append({
                    "kind": "special_episode",
                    "score": score,
                    "reasons": reasons,
                    "season": season_zero,
                    "episode": episode,
                    "season_id": season_zero.get("id"),
                    "season_number": 0,
                    "episode_id": episode.get("id"),
                    "episode_number": int(episode["number"]),
                })
        return candidates

    @staticmethod
    def _title_similarity(left, right):
        left = _normalize(left)
        right = _normalize(right)
        if not left or not right:
            return 0
        if left == right:
            return 100
        if left in right or right in left:
            return 55
        return 0

    @classmethod
    def _score_season(cls, item, show, season, normal_count):
        score = 0
        reasons = []
        item_date = item.get("release_date")
        season_date = season.get("air_date")
        delta = _days(item_date, season_date)
        if delta is not None:
            if delta == 0:
                score += 140; reasons.append("first-air-date exact")
            elif delta <= 7:
                score += 110; reasons.append("first-air-date <=7d")
            elif delta <= 31:
                score += 70; reasons.append("first-air-date <=31d")
            elif delta <= 90:
                score += 30; reasons.append("first-air-date <=90d")

        expected = item.get("episode_count")
        actual = len(season.get("episodes") or [])
        if expected is not None:
            expected = int(expected)
            if expected == actual and actual > 0:
                score += 120; reasons.append("episode-count exact")
            elif expected > 0 and actual > 0:
                penalty = min(90, abs(expected - actual) * 10)
                score -= penalty; reasons.append("episode-count mismatch")

        titles = [item.get("english_name"), item.get("romaji_name"), item.get("native_name")]
        show_match = max(cls._title_similarity(value, show.get("name")) for value in titles)
        if show_match == 100:
            score += 80; reasons.append("show-title exact")
        elif show_match:
            score += 40; reasons.append("show-title related")
        season_match = max(cls._title_similarity(value, season.get("name")) for value in titles)
        if season_match == 100:
            score += 70; reasons.append("season-title exact")
        elif season_match:
            score += 35; reasons.append("season-title related")

        media_format = str(item.get("media_format") or "").upper()
        if media_format in ("TV", "TV_SHORT"):
            score += 30; reasons.append("TV format supports season")
        elif media_format == "ONA":
            score += 10; reasons.append("ONA weakly supports season")

        # Safe structural fallback only for TV-like items.  A one-episode OVA
        # does not get pushed into the only normal season just because it exists.
        if normal_count == 1 and media_format in ("TV", "TV_SHORT", "ONA"):
            score += 90; reasons.append("only normal provider season")
        return score, reasons

    @classmethod
    def _score_special_episode(cls, item, episode):
        score = 0
        reasons = []
        delta = _days(item.get("release_date"), episode.get("air_date"))
        if delta is not None:
            if delta == 0:
                score += 140; reasons.append("special air-date exact")
            elif delta <= 7:
                score += 90; reasons.append("special air-date <=7d")
            elif delta <= 31:
                score += 50; reasons.append("special air-date <=31d")

        titles = [item.get("english_name"), item.get("romaji_name"), item.get("native_name")]
        title_match = max(cls._title_similarity(value, episode.get("name")) for value in titles)
        if title_match == 100:
            score += 120; reasons.append("special title exact")
        elif title_match:
            score += 65; reasons.append("special title related")

        expected = item.get("episode_count")
        if expected is not None:
            if int(expected) == 1:
                score += 70; reasons.append("single-episode item")
            elif int(expected) > 1:
                score -= 40; reasons.append("multi-episode item")

        media_format = str(item.get("media_format") or "").upper()
        if media_format in ("OVA", "MOVIE", "SPECIAL", "MUSIC"):
            score += 30; reasons.append("format supports special")
        return score, reasons

    @classmethod
    def _choose_candidate(cls, item, show, candidates):
        ranked = sorted(candidates, key=lambda value: value["score"], reverse=True)
        diagnostic = [
            "{}:S{}{}={} [{}]".format(
                candidate["kind"], candidate.get("season_number"),
                "E{}".format(candidate.get("episode_number"))
                if candidate.get("episode_number") is not None else "",
                candidate["score"], ", ".join(candidate.get("reasons") or []),
            )
            for candidate in ranked[:5]
        ]
        LOGGER.info(
            "Placement candidates for %s:%s %s in %s: %s",
            item.get("provider"), item.get("provider_item_id"),
            item.get("english_name") or item.get("romaji_name"),
            show.get("name"), diagnostic,
        )
        if not ranked or ranked[0]["score"] < cls.MIN_CONFIDENCE:
            raise MetadataProviderError(
                "Provider franchise has no confident placement for watchlist item"
            )
        if len(ranked) > 1 and ranked[0]["score"] - ranked[1]["score"] < cls.MIN_MARGIN:
            raise MetadataProviderError(
                "Provider franchise placement is ambiguous between {} and {}".format(
                    diagnostic[0], diagnostic[1]
                )
            )
        return dict(ranked[0])
