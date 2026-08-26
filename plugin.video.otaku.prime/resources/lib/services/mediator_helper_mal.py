# -*- coding: utf-8 -*-
"""MyAnimeList entry path into the Simkl-backed Prime mediator."""
from resources.lib.services.mediator_helper_simkl import SimklMediatorHelper


class MALMediatorHelper(SimklMediatorHelper):
    provider="mal"
