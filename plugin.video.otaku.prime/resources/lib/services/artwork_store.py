# -*- coding: utf-8 -*-
"""Prime-owned persistent artwork storage shared with Kodi and the web UI."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import tempfile
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import quote,unquote,urlsplit
from urllib.request import Request,urlopen

from resources.lib.logging_config import get_logger
from resources.lib.service_lifecycle import ServiceWorkHalted


LOGGER=get_logger(__name__)
SPECIAL_ROOT="special://masterprofile/otaku-prime/artwork"
WEB_ROOT="/api/artwork/"
MAX_ARTWORK_BYTES=25*1024*1024
IDENTITY_PRIORITY=("tvdb","simkl","anilist","mal","kitsu")
ARTWORK_FIELDS={
    "poster_url":"poster",
    "fanart_url":"fanart",
    "banner_url":"banner",
    "clearlogo_url":"clearlogo",
    "clearart_url":"clearart",
    "landscape_url":"landscape",
    "thumb_url":"thumb",
}


class ArtworkStoreError(RuntimeError):
    pass


def _default_root():
    try:
        import xbmcvfs
        translated=xbmcvfs.translatePath(SPECIAL_ROOT)
        if translated:
            return translated
    except (ImportError,RuntimeError,AttributeError):
        pass
    return os.path.join(os.path.expanduser("~"),".kodi","userdata",
                        "otaku-prime","artwork")


def _safe_token(value):
    text=str(value or "").strip().lower()
    return "".join(character for character in text
                   if character in "abcdefghijklmnopqrstuvwxyz0123456789-_")


def _image_format(payload,content_type=""):
    """Return a verified extension and MIME type from the downloaded bytes."""
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png","image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return ".jpg","image/jpeg"
    if len(payload)>=12 and payload[:4]==b"RIFF" and payload[8:12]==b"WEBP":
        return ".webp","image/webp"
    if payload.startswith((b"GIF87a",b"GIF89a")):
        return ".gif","image/gif"
    raise ArtworkStoreError(
        "downloaded payload is not a supported PNG, JPEG, WebP, or GIF image"
        +(" ({})".format(content_type) if content_type else ""))


class PersistentArtworkStore:
    """Download originals once and expose stable local references.

    The JSON manifests are deliberately outside Prime's SQLite database. They
    retain provider identities and origin URLs so artwork can be rediscovered
    after the catalogue database is rebuilt.
    """
    def __init__(self,root_path=None,special_root=SPECIAL_ROOT,timeout=30,
                 max_bytes=MAX_ARTWORK_BYTES,opener=None,halt_requested=None):
        self.root_path=os.path.abspath(str(root_path or _default_root()))
        self.special_root=str(special_root or SPECIAL_ROOT).rstrip("/")
        self.timeout=max(1,int(timeout)); self.max_bytes=max(1024,int(max_bytes))
        self._open=opener or urlopen
        self._external_halt_requested=halt_requested or (lambda:False)
        self._lock=threading.RLock()
        self._stop=threading.Event(); self._wake=threading.Event(); self._thread=None
        self._manifest_index=None

    @staticmethod
    def canonical_identity(ids):
        for provider in IDENTITY_PRIORITY:
            value=_safe_token((ids or {}).get(provider))
            if value:
                return "{}-{}".format(provider,value)
        raise ArtworkStoreError("artwork has no stable provider identity")

    @staticmethod
    def browser_url(media_type,identity,art_type):
        relative="{}/{}/{}".format(
            _safe_token(media_type),_safe_token(identity),_safe_token(art_type))
        return WEB_ROOT+quote(relative,safe="/-_.")

    def kodi_path(self,relative_path):
        relative=str(relative_path or "").replace(os.sep,"/").lstrip("/")
        return self.special_root+"/"+relative

    @staticmethod
    def _manifest_record(manifest,art_type,root_path):
        record=((manifest or {}).get("artwork") or {}).get(art_type)
        if not isinstance(record,dict): return None
        relative=str(record.get("file") or "").replace("\\","/").lstrip("/")
        if not relative: return None
        physical=os.path.abspath(os.path.join(root_path,*relative.split("/")))
        if os.path.commonpath((root_path,physical))!=root_path or not os.path.isfile(physical):
            return None
        return dict(record)

    @staticmethod
    def _load_json(path):
        try:
            with open(path,"r",encoding="utf-8") as handle:
                value=json.load(handle)
            return value if isinstance(value,dict) else {}
        except (OSError,ValueError,json.JSONDecodeError):
            return {}

    @staticmethod
    def _write_json(path,value):
        os.makedirs(os.path.dirname(path),exist_ok=True)
        handle=tempfile.NamedTemporaryFile(
            mode="w",encoding="utf-8",dir=os.path.dirname(path),
            prefix=".manifest-",suffix=".tmp",delete=False)
        temporary=handle.name
        try:
            with handle:
                json.dump(value,handle,ensure_ascii=False,sort_keys=True,indent=2)
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary,path)
        except Exception:
            try: os.unlink(temporary)
            except OSError: pass
            raise

    def _check_halt(self):
        if self._stop.is_set() or self._external_halt_requested():
            raise ServiceWorkHalted("artwork download halted for addon shutdown")

    def _download(self,url):
        parsed=urlsplit(str(url or "").strip())
        if parsed.scheme not in ("http","https") or not parsed.hostname:
            raise ArtworkStoreError("artwork source is not an HTTP(S) URL")
        self._check_halt()
        request=Request(url,headers={
            "Accept":"image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8",
            "User-Agent":"Otaku-Prime/0.1.2 persistent-artwork",
        })
        try:
            with self._open(request,timeout=self.timeout) as response:
                headers=getattr(response,"headers",{}) or {}
                content_type=str(headers.get("Content-Type","")).split(";",1)[0].strip().lower()
                try: declared=int(headers.get("Content-Length") or 0)
                except (TypeError,ValueError): declared=0
                if declared>self.max_bytes:
                    raise ArtworkStoreError("artwork exceeds the {} byte limit".format(
                        self.max_bytes))
                chunks=[]; size=0
                while True:
                    self._check_halt()
                    chunk=response.read(64*1024)
                    if not chunk: break
                    size+=len(chunk)
                    if size>self.max_bytes:
                        raise ArtworkStoreError("artwork exceeds the {} byte limit".format(
                            self.max_bytes))
                    chunks.append(chunk)
        except ServiceWorkHalted:
            raise
        except ArtworkStoreError:
            raise
        except HTTPError as exc:
            raise ArtworkStoreError("artwork server returned HTTP {}".format(exc.code)) from exc
        except (URLError,TimeoutError,OSError) as exc:
            raise ArtworkStoreError("artwork download failed: {}".format(exc)) from exc
        payload=b"".join(chunks)
        if not payload: raise ArtworkStoreError("artwork server returned an empty response")
        extension,mime_type=_image_format(payload,content_type)
        return payload,extension,mime_type

    def _manifest_entries(self):
        if not os.path.isdir(self.root_path): return []
        result=[]
        for directory,_,files in os.walk(self.root_path):
            if "manifest.json" not in files: continue
            path=os.path.join(directory,"manifest.json")
            manifest=self._load_json(path)
            if manifest: result.append((path,manifest))
        return result

    def _index_manifest(self,path,manifest):
        if self._manifest_index is None: return
        media_type=str(manifest.get("media_type") or "")
        for provider,value in (manifest.get("ids") or {}).items():
            if provider in IDENTITY_PRIORITY and value not in (None,""):
                self._manifest_index[(media_type,provider,str(value))]=path

    def _ensure_manifest_index(self):
        if self._manifest_index is not None: return
        self._manifest_index={}
        for path,manifest in self._manifest_entries():
            self._index_manifest(path,manifest)

    def _matching_manifest(self,media_type,ids):
        """Find a manifest by any stable provider ID, not a Prime local ID."""
        wanted={name:str(value) for name,value in (ids or {}).items()
                if name in IDENTITY_PRIORITY and value not in (None,"")}
        if not wanted: return None,None
        try:
            identity=self.canonical_identity(wanted)
        except ArtworkStoreError:
            identity=None
        if identity:
            direct=os.path.join(self.root_path,media_type,identity,"manifest.json")
            manifest=self._load_json(direct)
            if manifest:
                self._ensure_manifest_index(); self._index_manifest(direct,manifest)
                return direct,manifest
        self._ensure_manifest_index()
        for provider in IDENTITY_PRIORITY:
            value=wanted.get(provider)
            path=self._manifest_index.get((str(media_type),provider,value)) if value else None
            if path:
                manifest=self._load_json(path)
                if manifest: return path,manifest
        return None,None

    def existing(self,media_type,ids):
        """Return browser and Kodi paths recoverable without Prime's database."""
        media_type=_safe_token(media_type)
        with self._lock:
            _,manifest=self._matching_manifest(media_type,ids)
            if not manifest: return {}
            result={}
            for field,art_type in ARTWORK_FIELDS.items():
                record=self._manifest_record(manifest,art_type,self.root_path)
                if not record: continue
                result[field]=self.browser_url(
                    media_type,manifest.get("identity"),art_type)
                result.setdefault("kodi_paths",{})[art_type]=self.kodi_path(record["file"])
            return result

    def persist(self,media_type,ids,art_type,source_url):
        media_type=_safe_token(media_type)
        art_type=_safe_token(art_type)
        if media_type not in ("tvshows","movies","seasons","episodes"):
            raise ArtworkStoreError("unsupported artwork media type")
        if art_type not in set(ARTWORK_FIELDS.values()):
            raise ArtworkStoreError("unsupported artwork type")
        identity=self.canonical_identity(ids)
        source_url=str(source_url or "").strip()
        with self._lock:
            manifest_path,manifest=self._matching_manifest(media_type,ids)
            if manifest_path:
                item_directory=os.path.dirname(manifest_path)
                identity=str(manifest.get("identity") or os.path.basename(item_directory))
            else:
                item_directory=os.path.join(self.root_path,media_type,identity)
                manifest_path=os.path.join(item_directory,"manifest.json")
                manifest={}
            item_relative=os.path.relpath(item_directory,self.root_path).replace(os.sep,"/")
            cached=self._manifest_record(manifest,art_type,self.root_path)
            if cached and str(cached.get("source_url") or "")==source_url:
                cached["web_url"]=self.browser_url(media_type,identity,art_type)
                cached["kodi_path"]=self.kodi_path(cached["file"])
                LOGGER.info("Prime artwork cache hit: %s %s %s",media_type,identity,art_type)
                return cached

            LOGGER.info("Prime artwork download started: %s %s %s host=%s",
                        media_type,identity,art_type,urlsplit(source_url).hostname or "unknown")
            self._check_halt()
            artwork=manifest.setdefault("artwork",{})
            previous=artwork.get(art_type) if isinstance(artwork.get(art_type),dict) else {}
            try: attempts=int(previous.get("attempts") or 0)+1
            except (TypeError,ValueError): attempts=1
            pending={
                "source_url":source_url,"status":"downloading","attempts":attempts,
                "last_attempt_epoch":int(time.time()),"retry_after_epoch":0,
            }
            artwork[art_type]=pending
            manifest.update({
                "version":1,"media_type":media_type,"identity":identity,
                "ids":{name:str(value) for name,value in (ids or {}).items()
                       if name in IDENTITY_PRIORITY and value not in (None,"")},
            })
            self._write_json(manifest_path,manifest)
            self._index_manifest(manifest_path,manifest)
            try:
                payload,extension,mime_type=self._download(source_url)
            except ServiceWorkHalted:
                pending["status"]="pending"
                self._write_json(manifest_path,manifest)
                raise
            except ArtworkStoreError as exc:
                pending.update({
                    "status":"failed","last_error":str(exc),
                    "retry_after_epoch":int(time.time())+min(21600,300*(2**min(attempts-1,6))),
                })
                self._write_json(manifest_path,manifest)
                raise
            digest=hashlib.sha256(payload).hexdigest()
            filename="{}-{}{}".format(art_type,digest[:16],extension)
            relative="{}/{}".format(item_relative,filename)
            physical=os.path.join(item_directory,filename)
            os.makedirs(item_directory,exist_ok=True)
            if not os.path.isfile(physical):
                handle=tempfile.NamedTemporaryFile(
                    mode="wb",dir=item_directory,prefix=".artwork-",suffix=".tmp",
                    delete=False)
                temporary=handle.name
                try:
                    with handle:
                        handle.write(payload); handle.flush(); os.fsync(handle.fileno())
                    os.replace(temporary,physical)
                except Exception:
                    try: os.unlink(temporary)
                    except OSError: pass
                    raise
            record={
                "file":relative,"source_url":source_url,"sha256":digest,
                "mime_type":mime_type,"size":len(payload),"status":"ready",
                "attempts":attempts,"last_attempt_epoch":int(time.time()),
            }
            artwork[art_type]=record
            self._write_json(manifest_path,manifest)
            self._index_manifest(manifest_path,manifest)
            result=dict(record)
            result["web_url"]=self.browser_url(media_type,identity,art_type)
            result["kodi_path"]=self.kodi_path(relative)
            LOGGER.info("Prime artwork stored: %s %s %s bytes=%s sha256=%s",
                        media_type,identity,art_type,len(payload),digest[:16])
            return result

    def retry_pending(self,limit=3,now_epoch=None):
        """Retry a bounded number of failed downloads with persistent backoff."""
        now=int(now_epoch or time.time()); attempted=stored=0
        for _,manifest in self._manifest_entries():
            if attempted>=max(0,int(limit)): break
            media_type=str(manifest.get("media_type") or "")
            ids=manifest.get("ids") or {}
            for art_type,record in list((manifest.get("artwork") or {}).items()):
                if attempted>=max(0,int(limit)): break
                if not isinstance(record,dict) or record.get("status")=="ready": continue
                source_url=str(record.get("source_url") or "").strip()
                if not source_url or int(record.get("retry_after_epoch") or 0)>now: continue
                self._check_halt(); attempted+=1
                try:
                    self.persist(media_type,ids,art_type,source_url); stored+=1
                except ServiceWorkHalted:
                    raise
                except ArtworkStoreError as exc:
                    LOGGER.info("Prime artwork retry remains pending: %s %s %s: %s",
                                media_type,manifest.get("identity"),art_type,exc)
        return {"attempted":attempted,"stored":stored}

    def _run(self,retry_interval):
        while not self._stop.is_set():
            try:
                result=self.retry_pending(limit=3)
                if result["attempted"]:
                    LOGGER.info("Prime artwork retry pass complete: attempted=%s stored=%s",
                                result["attempted"],result["stored"])
            except ServiceWorkHalted:
                break
            except Exception:
                LOGGER.exception("Prime artwork retry pass failed")
            self._wake.wait(max(30.0,float(retry_interval))); self._wake.clear()

    def start(self,retry_interval=300):
        if self._thread and self._thread.is_alive():
            return {"scheduled":False,"busy":True}
        self._stop.clear(); self._wake.clear()
        self._thread=threading.Thread(
            target=self._run,args=(retry_interval,),
            name="OtakuPrimeArtworkStore",daemon=True)
        self._thread.start()
        return {"scheduled":True,"busy":False}

    def stop(self,timeout=3):
        self.request_stop()
        if self._thread: self._thread.join(timeout=max(0.0,float(timeout)))
        return not (self._thread and self._thread.is_alive())

    def request_stop(self):
        self._stop.set(); self._wake.set()

    def resolve_web_path(self,request_path):
        relative=unquote(str(request_path or "")).replace("\\","/").lstrip("/")
        parts=relative.split("/")
        if (len(parts)!=3 or any(part in ("",".","..") for part in parts)
                or any(_safe_token(part)!=part for part in parts)):
            return None
        media_type,identity,art_type=parts
        manifest=self._load_json(os.path.join(
            self.root_path,media_type,identity,"manifest.json"))
        record=self._manifest_record(manifest,art_type,self.root_path)
        if not record: return None
        physical=os.path.abspath(os.path.join(
            self.root_path,*str(record["file"]).replace("\\","/").split("/")))
        mime_type=mimetypes.guess_type(physical)[0] or "application/octet-stream"
        if not mime_type.startswith("image/"): return None
        return physical,mime_type

    def manifests(self):
        """Return recovery metadata without depending on Prime's SQLite DB."""
        return [manifest for _,manifest in self._manifest_entries()]
