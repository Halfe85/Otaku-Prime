# -*- coding: utf-8 -*-
"""Runtime metadata resolver with provider-order-aware TheTVDB placement.

The base resolver owns generic TMDB/TheTVDB matching.  TheTVDB needs one extra
normalization step: staged AniList entries must be placed against the series'
default episode order, not against incomplete season summary dates returned by
``/series/{id}/extended``.
"""
from __future__ import annotations

from resources.lib.services.metadata_resolver import (
    MetadataProviderError,
    MetadataResolverService as BaseMetadataResolverService,
    TMDBMetadataClient,
    TVDBMetadataClient,
    _year,
)


class TVDBDefaultOrderMetadataClient(TVDBMetadataClient):
    """Expose TheTVDB's default episode order as reliable season summaries."""

    def search_series(self, title, year=None):
        """Match Kodi's TVDB scraper: try year first, then fall back without it."""
        params = {"query": title, "type": "series", "limit": 20}
        if year:
            params["year"] = int(year)
        payload = self._request("/search", params=params)
        rows = payload.get("data") or []
        if not rows and year:
            payload = self._request("/search", params={
                "query": title,
                "type": "series",
                "limit": 20,
            })
            rows = payload.get("data") or []

        results = []
        for item in rows:
            identifier = item.get("tvdb_id")
            if identifier in (None, ""):
                identifier = item.get("id")
                if isinstance(identifier, str) and identifier.startswith("series-"):
                    identifier = identifier.split("-", 1)[1]
            if identifier in (None, ""):
                continue
            results.append({
                "id": identifier,
                "name": item.get("name") or item.get("seriesName"),
                "original_name": item.get("name") or item.get("seriesName"),
                "aliases": self._aliases(item),
                "year": _year(
                    item.get("first_air_time")
                    or item.get("year")
                    or item.get("firstAired")
                ),
            })
        return results

    @staticmethod
    def _season_type_id(item):
        type_value = item.get("type") or {}
        if isinstance(type_value, dict):
            value = type_value.get("id")
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        try:
            return int(type_value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _season_type_text(item):
        type_value = item.get("type") or {}
        if isinstance(type_value, dict):
            return " ".join(str(value or "") for value in (
                type_value.get("name"), type_value.get("type"),
                type_value.get("alternateName"),
            )).lower()
        return str(type_value or "").lower()

    def _default_season_metadata(self, data):
        """Return one metadata season per number for the series' default order."""
        default_type = data.get("defaultSeasonType")
        try:
            default_type = int(default_type) if default_type is not None else None
        except (TypeError, ValueError):
            default_type = None

        selected = {}
        fallback = {}
        for item in data.get("seasons") or []:
            number = item.get("number")
            if number is None:
                number = item.get("seasonNumber")
            if number is None:
                continue
            number = int(number)
            fallback.setdefault(number, item)
            type_id = self._season_type_id(item)
            type_text = self._season_type_text(item)
            is_default = (
                (default_type is not None and type_id == default_type)
                or "aired order" in type_text
                or "default" in type_text
            )
            if is_default:
                selected[number] = item
        return selected or fallback

    def get_show(self, show_id):
        payload = self._request("/series/{}/extended".format(show_id))
        data = payload.get("data") or {}
        season_meta = self._default_season_metadata(data)
        episodes = self._default_episodes(show_id)

        grouped = {}
        for episode in episodes:
            number = int(episode["season_number"])
            group = grouped.setdefault(number, {
                "number": number,
                "air_dates": [],
                "episode_count": 0,
            })
            group["episode_count"] += 1
            air_date = str(episode.get("air_date") or "")[:10]
            if air_date:
                group["air_dates"].append(air_date)

        # Default-order episodes are the authority for which season numbers
        # exist.  The extended season object only contributes ID/name metadata.
        seasons = []
        for number in sorted(grouped):
            group = grouped[number]
            meta = season_meta.get(number) or {}
            air_date = min(group["air_dates"]) if group["air_dates"] else None
            seasons.append({
                "id": meta.get("id"),
                "number": number,
                "name": meta.get("name") or ("Specials" if number == 0 else "Season {}".format(number)),
                "air_date": air_date,
                "episode_count": group["episode_count"],
            })

        # Extremely new/unreleased entries can have a season record before any
        # episode exists. Keep those as a fallback, but do not let them override
        # a default-order episode-derived summary.
        known = {item["number"] for item in seasons}
        for number, meta in sorted(season_meta.items()):
            if number in known:
                continue
            seasons.append({
                "id": meta.get("id"),
                "number": number,
                "name": meta.get("name") or ("Specials" if number == 0 else "Season {}".format(number)),
                "air_date": meta.get("firstAired") or meta.get("first_aired"),
                "episode_count": 0,
            })

        return {
            "id": data.get("id") or show_id,
            "name": data.get("name") or data.get("seriesName"),
            "original_name": data.get("name") or data.get("seriesName"),
            "year": _year(data.get("firstAired") or data.get("first_air_time") or data.get("year")),
            "seasons": seasons,
        }


class MetadataResolverService(BaseMetadataResolverService):
    """Production resolver using TheTVDB default-order season placement."""

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

    @staticmethod
    def _best_staged_season(staged, candidates):
        # First use the strict base date scorer. With TVDB candidates derived
        # from episode dates this normally resolves the item immediately.
        match = BaseMetadataResolverService._best_staged_season(staged, candidates)
        if match:
            return match

        # A confidently matched provider franchise with exactly one ordinary
        # season has only one valid placement for a non-special staged TV item.
        ordinary = [
            item for item in candidates
            if item.get("number") is not None and int(item["number"]) != 0
        ]
        if len(ordinary) == 1:
            return ordinary[0]
        return None
