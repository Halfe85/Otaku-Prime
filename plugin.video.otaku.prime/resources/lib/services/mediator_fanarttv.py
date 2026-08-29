# -*- coding: utf-8 -*-
"""Fanart.tv URL enrichment for Prime TV-show catalogue placements.

This module never downloads image bytes.  It selects stable Fanart.tv asset
URLs and stores them in Prime; the web browser remains responsible for normal
HTTP image caching.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from urllib.error import HTTPError,URLError
from urllib.parse import quote
from urllib.request import Request,urlopen

from resources.lib.logging_config import get_logger

LOGGER=get_logger(__name__)
FANARTTV_API_URL="https://webservice.fanart.tv/v3.2/tv"
API_KEY_ENV="OTAKU_PRIME_FANARTTV_API_KEY"
CLIENT_KEY_ENV="OTAKU_PRIME_FANARTTV_CLIENT_KEY"
INFO_DB_ENV="OTAKU_PRIME_OTAKU_INFO_DB"
CONTEXT_ADDON_ID="context.otaku"


class FanartTVError(RuntimeError):
    pass


def _context_info_db(explicit=None):
    path=str(explicit or os.environ.get(INFO_DB_ENV,"")).strip()
    if path: return path
    try:
        import xbmcaddon
        import xbmcvfs
        addon_path=xbmcaddon.Addon(CONTEXT_ADDON_ID).getAddonInfo("path")
        path=xbmcvfs.translatePath(os.path.join(addon_path,"info.db"))
    except (ImportError,RuntimeError,AttributeError):
        path=os.path.join(
            os.path.expanduser("~"),".kodi","addons",CONTEXT_ADDON_ID,"info.db")
    return path


def _packaged_project_key(info_db=None):
    """Read Otaku's packaged project key without copying it into Prime."""
    path=_context_info_db(info_db)
    if not path or not os.path.isfile(path): return ""
    uri="file:{}?mode=ro&immutable=1".format(quote(os.path.abspath(path)))
    try:
        with sqlite3.connect(uri,uri=True) as db:
            row=db.execute(
                "SELECT api_key FROM info WHERE api_name=?",("Fanart-TV",)
            ).fetchone()
    except sqlite3.Error as exc:
        LOGGER.warning("Could not read Fanart.tv project configuration from context.otaku: %s",exc)
        return ""
    return str(row[0] or "").strip() if row else ""


def _likes(row):
    try: return int((row or {}).get("likes") or 0)
    except (TypeError,ValueError): return 0


def _select(payload,types,languages=("en","ja","00","")):
    """Choose one URL by image type, language priority, then community likes."""
    candidates=[]
    for type_index,name in enumerate(types):
        for row in (payload or {}).get(name) or []:
            if not isinstance(row,dict) or not str(row.get("url") or "").startswith(
                    ("https://","http://")):
                continue
            lang=str(row.get("lang") or "")
            try: language_index=languages.index(lang)
            except ValueError: language_index=len(languages)
            candidates.append((type_index,language_index,-_likes(row),str(row["url"])))
    return min(candidates)[3] if candidates else None


class FanartTVClient:
    """Small TheTVDB-ID Fanart.tv client with an in-memory response cache."""
    def __init__(self,api_key=None,client_key=None,timeout=30,opener=None,info_db=None):
        configured_key=str(api_key or os.environ.get(API_KEY_ENV,"")).strip()
        self.api_key=configured_key or _packaged_project_key(info_db)
        if self.api_key and not configured_key:
            LOGGER.info("Using the Fanart.tv project key packaged by context.otaku")
        self.client_key=str(client_key or os.environ.get(CLIENT_KEY_ENV,"")).strip()
        self.timeout=int(timeout); self._open=opener or urlopen
        self._cache={}; self._lock=threading.Lock()

    @property
    def configured(self):
        return bool(self.api_key)

    def tv(self,tvdb_id):
        key=str(tvdb_id or "").strip()
        if not key: return {}
        if not self.configured: return {}
        with self._lock:
            if key in self._cache: return self._cache[key]
        headers={"Accept":"application/json","User-Agent":"Otaku-Prime/0.1.2 fanarttv"}
        if self.api_key: headers["api-key"]=self.api_key
        if self.client_key: headers["client-key"]=self.client_key
        try:
            with self._open(Request(FANARTTV_API_URL+"/"+key,headers=headers),
                            timeout=self.timeout) as response:
                payload=json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code==404: payload={}
            else: raise FanartTVError(
                "Fanart.tv TV {} returned HTTP {}".format(key,exc.code)) from exc
        except (URLError,TimeoutError,OSError,ValueError,json.JSONDecodeError) as exc:
            raise FanartTVError("Fanart.tv TV {} failed: {}".format(key,exc)) from exc
        if not isinstance(payload,dict):
            raise FanartTVError("Fanart.tv returned an invalid TV artwork response")
        with self._lock: self._cache[key]=payload
        return payload


class FanartTVMediator:
    """Enrich a resolved Prime placement without affecting placement success."""
    def __init__(self,client=None):
        self.client=client or FanartTVClient(); self._missing_key_logged=False

    @staticmethod
    def artwork(payload):
        return {
            "poster_url":_select(payload,("tvposter",)),
            "clearlogo_url":_select(payload,("clearlogo","hdtvlogo")),
            "banner_url":_select(payload,("tvbanner","showbackground")),
        }

    def enrich(self,placement):
        show=(placement or {}).get("tv_show") or {}
        tvdb_id=show.get("tvdb_id")
        if tvdb_id in (None,""): return placement
        if not self.client.configured:
            if not self._missing_key_logged:
                LOGGER.warning(
                    "Fanart.tv artwork is inactive: configure %s with a project API key",
                    API_KEY_ENV)
                self._missing_key_logged=True
            return placement
        try: artwork=self.artwork(self.client.tv(tvdb_id))
        except FanartTVError as exc:
            LOGGER.warning("Fanart.tv artwork unavailable for TVDB %s: %s",tvdb_id,exc)
            return placement
        for key,value in artwork.items():
            if value: show[key]=value
        show["artwork_source"]="fanarttv" if any(artwork.values()) else show.get("artwork_source")
        return placement
