# -*- coding: utf-8 -*-
"""Native AniList relation path for Prime mediation."""
from resources.lib.services.mediator_helper_simkl import MediatorPlacementError


class AniListMediatorHelper:
    provider="anilist"
    def resolve(self,item,client=None):
        raise MediatorPlacementError(
            "Native AniList plus MAL structural mediation is not implemented yet")
