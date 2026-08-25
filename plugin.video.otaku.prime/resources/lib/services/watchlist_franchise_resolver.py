# -*- coding: utf-8 -*-
"""Resolve canonical watchlist rows to franchise roots before metadata placement."""
from __future__ import annotations

from resources.lib.services.anilist_relations import (
    AniListFranchiseResolverService,
    AniListRelationClient,
)


class UnifiedAniListFranchiseResolverService(AniListFranchiseResolverService):
    """AniList relation resolver for the canonical multi-provider watchlist table.

    This stage has exactly one catalogue side effect: create/update the franchise
    row.  It never creates a season or episode.  The selected TMDB/TheTVDB
    authority decides placement later from its complete franchise structure.
    """

    def __init__(self, media_store, watchlist_store, client=None, max_nodes=100):
        super().__init__(
            media_store,
            client=client or AniListRelationClient(),
            max_nodes=max_nodes,
            stage_only=True,
        )
        self.watchlist_store = watchlist_store
        self.watchlist_store.initialize()

    def run_once(self):
        rows = self.watchlist_store.list_relation_pending("anilist")
        active = []
        failed = []
        franchises = set()
        if self._stopping():
            return {"resolved": 0, "failed": [], "franchises": 0, "cancelled": True}
        if not rows:
            return {"resolved": 0, "failed": [], "franchises": 0, "staged_only": True}

        try:
            self._load_relation_graph([row["provider_item_id"] for row in rows])
        except Exception as exc:
            return {
                "resolved": 0,
                "failed": [{"provider": "anilist", "provider_item_id": None,
                            "error": str(exc)}],
                "franchises": 0,
            }

        for row in rows:
            if self._stopping():
                return {
                    "resolved": len(active),
                    "failed": failed,
                    "franchises": len(franchises),
                    "cancelled": True,
                }
            item_id = str(row["provider_item_id"])
            try:
                resolution = self._resolve(item_id)
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
                    "anilist", item_id, franchise_id, resolution
                )

                # Keep the Alpha8 staging mirror updated while old admin/tests are
                # still being migrated. Failure here must not invalidate the new
                # canonical pipeline.
                try:
                    self.relation_store.save_resolution(item_id, franchise_id, resolution)
                except KeyError:
                    pass

                franchises.add(franchise_id)
                active.append(item_id)
            except Exception as exc:
                failed.append({
                    "provider": "anilist",
                    "provider_item_id": item_id,
                    "error": str(exc),
                })

        return {
            "resolved": len(active),
            "failed": failed,
            "franchises": len(franchises),
            "staged_only": True,
        }
