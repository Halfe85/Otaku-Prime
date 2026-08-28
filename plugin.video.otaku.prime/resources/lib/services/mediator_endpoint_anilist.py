# -*- coding: utf-8 -*-
"""Exact-ID AniList metadata endpoint for the mediator."""
from resources.lib.services.mediator_helper_anilist import AniListMediatorHelper


class AniListMediatorEndpoint(AniListMediatorHelper):
    provider="anilist"

    @staticmethod
    def available(item):
        return item.get("anilist_id") not in (None,"")
