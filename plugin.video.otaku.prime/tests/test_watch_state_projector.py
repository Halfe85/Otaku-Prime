# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import tempfile
import unittest

from resources.lib.database.catalog import CatalogStore
from resources.lib.services.watch_state_projector import CatalogWatchStateProjector
from resources.lib.services.watchlist_watchdog import (
    WatchlistWatchdogService,
    WatchlistWatchdogStore,
)
from resources.lib.service_lifecycle import ServiceWorkHalted


class SegmentFactory:
    def __init__(self):
        self.value=0

    def __call__(self):
        self.value+=1
        return "{:06x}".format(self.value)


class Writer:
    def push(self,provider,item,entry):
        return {"provider":provider,"updated":True,
                "status":item["status"],"progress":item["progress"]}


class WatchStateProjectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,"users.sqlite")
        self.watchlist=WatchlistWatchdogStore(self.path)
        self.watchlist.initialize()
        self.watchlist.replace_provider_snapshot("anilist",[{
            "provider_item_id":"100","ids":{"anilist":"100","simkl":"400"},
            "english_name":"Example Season","list_status":"CURRENT",
            "progress":1,"episode_count":3,"media_format":"TV","raw":{},
        }])
        self.watchlist.finalize_merge()
        self.item=self.watchlist.list_all()[0]
        self.catalog=CatalogStore(self.path,SegmentFactory())
        self.catalog.initialize()
        series=self.catalog.get_or_create_series(
            english_name="Example",root_anilist_id="99",source_provider="anilist")
        season=self.catalog.add_watchlist_season(
            series["local_id"],self.item,season_number=1,
            provider_path="anilist",placement_source="relations",
            first_episode=1,last_episode=3)
        self.episodes=[self.catalog.add_episode(
            season["local_id"],number,source_episode_number=number)
            for number in (1,2,3)]
        self.manager=WatchlistWatchdogService([],self.watchlist,Writer())
        self.projector=CatalogWatchStateProjector(self.catalog,self.manager)
        self.manager.subscribe(self.projector.handle_watchlist_event)

    def tearDown(self):
        self.tmp.cleanup()

    def statuses(self):
        return [self.catalog.episode_watch_context(row["local_id"])["watch_status"]
                for row in self.episodes]

    def test_provider_progress_events_project_to_catalogue(self):
        result=self.projector.project_all([self.item])
        self.assertEqual((3,1),(result["episode_count"],result["watched_count"]))
        self.assertEqual([1,0,0],self.statuses())

        self.manager.update_item(self.item["local_id"],progress=3,source="remote-test")
        self.assertEqual([1,1,1],self.statuses())

    def test_episode_toggle_cascades_through_canonical_progress(self):
        self.projector.project_all([self.item])

        watched=self.projector.update_episode(self.episodes[2]["local_id"],True)
        self.assertEqual(3,watched["progress"])
        self.assertEqual([1,1,1],self.statuses())

        unwatched=self.projector.update_episode(self.episodes[1]["local_id"],False)
        self.assertEqual(1,unwatched["progress"])
        self.assertEqual([1,0,0],self.statuses())

    def test_startup_projection_stops_before_next_item_on_kodi_abort(self):
        halted=[False]
        projector=CatalogWatchStateProjector(
            self.catalog,self.manager,halt_requested=lambda:halted[0])
        original=projector.project_item
        calls=[]
        def project(item):
            calls.append(item["local_id"])
            halted[0]=True
            return original(item)
        projector.project_item=project
        with self.assertRaises(ServiceWorkHalted):
            projector.project_all([self.item,dict(self.item,local_id="second")])
        self.assertEqual([self.item["local_id"]],calls)


if __name__=="__main__":
    unittest.main()
