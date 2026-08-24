import os
import sys
import unittest

ROOT=os.path.dirname(os.path.dirname(__file__)); sys.path.insert(0,ROOT)
from resources.lib.services.startup_pipeline import StartupPipelineService


class Component:
    def __init__(self,name,events): self.name=name; self.events=events
    def run_once(self): self.events.append(self.name); return self.name
    def start(self,run_immediately=True): self.events.append((self.name,run_immediately))
    def stop(self,timeout=5): self.events.append(("stop",self.name))

class Mediator:
    def __init__(self,events): self.events=events
    def start(self): self.events.append("kodi-links"); return {}

class StartupPipelineTests(unittest.TestCase):
    def test_initial_chain_precedes_periodic_watchdogs(self):
        events=[]; watchlist=Component("watchlist",events); release=Component("release",events)
        pipeline=StartupPipelineService(watchlist,release,Mediator(events))
        pipeline.start(); pipeline._thread.join(timeout=2)
        self.assertEqual(["watchlist","release","kodi-links",
          ("watchlist",False),("release",False)],events)
        pipeline.stop()

if __name__=="__main__": unittest.main()
