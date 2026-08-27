# -*- coding: utf-8 -*-
"""Native Kitsu fallback path for Prime mediation."""
from resources.lib.services.mediator_helper_simkl import MediatorPlacementError


class KitsuMediatorHelper:
    provider="kitsu"
    def resolve(self,item,client=None):
        raise MediatorPlacementError("Native Kitsu structural mediation is not implemented yet")
