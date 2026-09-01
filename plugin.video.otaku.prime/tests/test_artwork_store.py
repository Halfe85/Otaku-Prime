import json
import os
import tempfile
import unittest

from resources.lib.service_lifecycle import ServiceWorkHalted
from resources.lib.services.artwork_store import (
    ArtworkStoreError,
    PersistentArtworkStore,
)


JPEG=b"\xff\xd8\xff\xe0"+b"prime-artwork"*32+b"\xff\xd9"


class Response:
    def __init__(self,payload,content_type="image/jpeg"):
        self.payload=payload; self.offset=0
        self.headers={"Content-Type":content_type,"Content-Length":str(len(payload))}
    def __enter__(self): return self
    def __exit__(self,*_args): return False
    def read(self,size=-1):
        if self.offset>=len(self.payload): return b""
        end=len(self.payload) if size<0 else min(len(self.payload),self.offset+size)
        value=self.payload[self.offset:end]; self.offset=end; return value


class PersistentArtworkStoreTests(unittest.TestCase):
    def test_original_is_saved_with_recovery_manifest_and_local_urls(self):
        calls=[]
        def opener(request,timeout=None):
            calls.append((request.full_url,timeout)); return Response(JPEG)
        with tempfile.TemporaryDirectory() as directory:
            store=PersistentArtworkStore(
                root_path=directory,special_root="special://profile/prime-art",
                opener=opener,timeout=9)
            first=store.persist("tvshows",{"tvdb":"371310","anilist":"108465"},
                                "poster","https://assets.example/poster.jpg")
            second=store.persist("tvshows",{"tvdb":"371310","anilist":"108465"},
                                 "poster","https://assets.example/poster.jpg")

            self.assertEqual(first["web_url"],second["web_url"])
            self.assertEqual(1,len(calls))
            self.assertEqual("/api/artwork/tvshows/tvdb-371310/poster",first["web_url"])
            self.assertTrue(first["kodi_path"].startswith(
                "special://profile/prime-art/tvshows/tvdb-371310/"))
            physical,mime=store.resolve_web_path(
                first["web_url"][len("/api/artwork/"):])
            self.assertEqual("image/jpeg",mime)
            with open(physical,"rb") as handle:
                self.assertEqual(JPEG,handle.read())
            manifests=store.manifests()
            self.assertEqual(1,len(manifests))
            self.assertEqual("371310",manifests[0]["ids"]["tvdb"])
            self.assertEqual("108465",manifests[0]["ids"]["anilist"])
            self.assertEqual("https://assets.example/poster.jpg",
                             manifests[0]["artwork"]["poster"]["source_url"])
            recovered=PersistentArtworkStore(
                root_path=directory,special_root="special://profile/prime-art",
                opener=lambda *_args,**_kwargs:self.fail("recovery used the network"))
            existing=recovered.existing(
                "tvshows",{"anilist":"108465","tvdb":"371310"})
            self.assertEqual(first["web_url"],existing["poster_url"])
            self.assertEqual(first["kodi_path"],existing["kodi_paths"]["poster"])

    def test_non_image_response_is_never_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            store=PersistentArtworkStore(
                root_path=directory,
                opener=lambda *_args,**_kwargs:Response(b"blocked","text/html"))
            with self.assertRaisesRegex(ArtworkStoreError,"not a supported"):
                store.persist("movies",{"anilist":"20954"},"poster",
                              "https://assets.example/blocked")
            manifests=store.manifests()
            self.assertEqual(1,len(manifests))
            self.assertEqual("failed",manifests[0]["artwork"]["poster"]["status"])
            self.assertIn("not a supported",
                          manifests[0]["artwork"]["poster"]["last_error"])

    def test_failed_manifest_is_retried_without_sqlite_state(self):
        responses=[Response(b"blocked","text/html"),Response(JPEG)]
        with tempfile.TemporaryDirectory() as directory:
            store=PersistentArtworkStore(
                root_path=directory,opener=lambda *_args,**_kwargs:responses.pop(0))
            with self.assertRaises(ArtworkStoreError):
                store.persist("tvshows",{"tvdb":"74796"},"clearlogo",
                              "https://assets.example/logo.png")
            pending=store.manifests()[0]["artwork"]["clearlogo"]

            result=store.retry_pending(
                limit=1,now_epoch=int(pending["retry_after_epoch"])+1)

            self.assertEqual({"attempted":1,"stored":1},result)
            self.assertEqual("ready",
                             store.manifests()[0]["artwork"]["clearlogo"]["status"])

    def test_shutdown_halts_before_network_or_disk_work(self):
        calls=[]
        with tempfile.TemporaryDirectory() as directory:
            store=PersistentArtworkStore(
                root_path=directory,halt_requested=lambda:True,
                opener=lambda *_args,**_kwargs:calls.append(True))
            with self.assertRaises(ServiceWorkHalted):
                store.persist("tvshows",{"tvdb":"74796"},"poster",
                              "https://assets.example/poster.jpg")
            self.assertEqual([],calls)
            self.assertFalse(os.path.exists(os.path.join(directory,"tvshows")))

    def test_manifest_and_traversal_paths_are_not_web_accessible(self):
        with tempfile.TemporaryDirectory() as directory:
            os.makedirs(os.path.join(directory,"tvshows","tvdb-1"))
            with open(os.path.join(directory,"tvshows","tvdb-1","manifest.json"),
                      "w",encoding="utf-8") as handle:
                json.dump({},handle)
            store=PersistentArtworkStore(root_path=directory)
            self.assertIsNone(store.resolve_web_path("../users.sqlite"))
            self.assertIsNone(store.resolve_web_path(
                "tvshows/tvdb-1/manifest.json"))


if __name__=="__main__": unittest.main()
