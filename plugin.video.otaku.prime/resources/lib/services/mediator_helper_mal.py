# -*- coding: utf-8 -*-
"""Native MyAnimeList corroboration path for Prime mediation."""
from resources.lib.services.mediator_helper_simkl import MediatorPlacementError


class MALMediatorHelper:
    provider="mal"
    def resolve(self,item,client=None):
        raise MediatorPlacementError(
            "Native AniList plus MAL structural mediation is not implemented yet")
