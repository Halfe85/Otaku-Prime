# -*- coding: utf-8 -*-
"""Kitsu entry path into the Simkl-backed Prime mediator."""
from resources.lib.services.mediator_helper_simkl import SimklMediatorHelper


class KitsuMediatorHelper(SimklMediatorHelper):
    provider="kitsu"
