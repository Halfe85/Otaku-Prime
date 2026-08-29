# -*- coding: utf-8 -*-
"""Exact-ID AniList metadata endpoint for the mediator."""
from resources.lib.services.mediator_helper_anilist import (
    AniListMediatorHelper,
    _metadata_terms,
)


class AniListMediatorEndpoint(AniListMediatorHelper):
    provider="anilist"

    @staticmethod
    def available(item):
        return item.get("anilist_id") not in (None,"")

    def cast(self,anilist_id):
        return self.client.cast(anilist_id)

    def poster(self,anilist_id):
        cover=(self.client.media(anilist_id) or {}).get("coverImage") or {}
        return cover.get("extraLarge") or cover.get("large") or cover.get("medium")

    def classification(self,anilist_id):
        media=self.client.media(anilist_id) or {}
        return _metadata_terms(media,media)
