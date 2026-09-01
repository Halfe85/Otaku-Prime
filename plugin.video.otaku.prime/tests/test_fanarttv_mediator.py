import json
import os
import sqlite3
import tempfile
import unittest

from resources.lib.services.mediator_fanarttv import (
    FanartTVClient,
    FanartTVMediator,
)


class Response:
    def __init__(self,payload): self.payload=payload
    def __enter__(self): return self
    def __exit__(self,*_args): return False
    def read(self): return json.dumps(self.payload).encode("utf-8")


class FanartTVMediatorTests(unittest.TestCase):
    def payload(self):
        return {
            "tvposter":[
                {"url":"https://assets/poster-ja.jpg","lang":"ja","likes":"99"},
                {"url":"https://assets/poster-en-low.jpg","lang":"en","likes":"2"},
                {"url":"https://assets/poster-en-high.jpg","lang":"en","likes":"8"},
            ],
            "clearlogo":[{"url":"https://assets/clearlogo.png","lang":"en","likes":"1"}],
            "hdtvlogo":[{"url":"https://assets/hdtvlogo.png","lang":"en","likes":"99"}],
            "tvbanner":[{"url":"https://assets/banner.jpg","lang":"00","likes":"5"}],
            "showbackground":[{"url":"https://assets/fanart.jpg","lang":"00","likes":"7"}],
        }

    def test_client_uses_tvdb_id_and_returns_only_json_metadata(self):
        calls=[]
        def open_request(request,timeout=None):
            calls.append((request.full_url,dict(request.header_items()),timeout))
            return Response(self.payload())
        client=FanartTVClient(api_key="project",timeout=7,opener=open_request)

        payload=client.tv("74796")
        cached=client.tv("74796")

        self.assertIs(payload,cached)
        self.assertEqual(1,len(calls))
        self.assertEqual("https://webservice.fanart.tv/v3.2/tv/74796",calls[0][0])
        self.assertEqual("project",dict(calls[0][1])["Api-key"])

    def test_client_loads_otaku_packaged_project_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path=os.path.join(directory,"info.db")
            with sqlite3.connect(path) as db:
                db.execute("""CREATE TABLE info(
                  api_name TEXT PRIMARY KEY,api_key TEXT)""")
                db.execute("INSERT INTO info(api_name,api_key) VALUES(?,?)",
                           ("Fanart-TV","otaku-project-key"))

            with self.assertLogs("otaku_prime.services-mediator_fanarttv",level="INFO"):
                client=FanartTVClient(info_db=path)

        self.assertEqual("otaku-project-key",client.api_key)
        self.assertTrue(client.configured)

    def test_artwork_prefers_language_then_likes_and_enriches_urls(self):
        client=type("Client",(),{
            "configured":True,
            "tv":lambda _self,_tvdb_id:self.payload(),
        })()
        placement={"tv_show":{"tvdb_id":"74796","poster_url":"provider.jpg"}}

        with self.assertLogs("otaku_prime.services-mediator_fanarttv",level="INFO") as logs:
            FanartTVMediator(client).enrich(placement)

        show=placement["tv_show"]
        self.assertEqual("https://assets/poster-en-high.jpg",show["poster_url"])
        self.assertEqual("https://assets/fanart.jpg",show["fanart_url"])
        self.assertEqual("https://assets/clearlogo.png",show["clearlogo_url"])
        self.assertEqual("https://assets/banner.jpg",show["banner_url"])
        self.assertEqual("fanarttv",show["artwork_source"])
        self.assertIn("Fanart.tv artwork selected for TVDB 74796","\n".join(logs.output))

    def test_unconfigured_fanart_is_non_fatal_and_keeps_provider_fallback(self):
        client=type("Client",(),{"configured":False})()
        placement={"tv_show":{"tvdb_id":"74796","poster_url":"fallback.jpg"}}
        with self.assertLogs("otaku_prime.services-mediator_fanarttv",level="WARNING"):
            result=FanartTVMediator(client).enrich(placement)
        self.assertIs(placement,result)
        self.assertEqual("fallback.jpg",placement["tv_show"]["poster_url"])


if __name__=="__main__": unittest.main()
