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
    def inventory(self): self.events.append("kodi-inventory"); return {}
    def reconcile(self): self.events.append("kodi-reconcile"); return {}

class StartupPipelineTests(unittest.TestCase):
    def test_initial_chain_precedes_periodic_watchdogs(self):
        events=[]; watchlist=Component("watchlist",events)
        pipeline=StartupPipelineService(watchlist,Mediator(events))
        pipeline.start(); pipeline._thread.join(timeout=2)
        self.assertEqual(["kodi-inventory","watchlist","kodi-reconcile",
          ("watchlist",False)],events)
        pipeline.stop()

if __name__=="__main__": unittest.main()
