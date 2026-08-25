import json
import os
import sys
import unittest


ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from resources.lib.services.kodi_db_middleware import KodiDbMiddleware
from resources.lib.services.mediator_service import MediatorService
class KodiDbMiddlewareTests(unittest.TestCase):
    def test_updates_watch_state_through_json_rpc(self):
        requests = []

        def execute(payload):
            request = json.loads(payload)
            requests.append(request)
            return json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": "OK"})

        middleware = KodiDbMiddleware(object(), execute)
        middleware.set_episode_watched(12, True)
        middleware.set_series_watched(11, True)
        self.assertEqual(
            [
                "VideoLibrary.SetEpisodeDetails",
                "VideoLibrary.SetTVShowDetails",
            ],
            [request["method"] for request in requests],
        )
        self.assertEqual(1, requests[0]["params"]["playcount"])

    def test_mediator_updates_sqlite_before_kodi(self):
        events = []

        class Store:
            def set_watch_status(self, media_type, local_id, watched):
                events.append(("sqlite", media_type, local_id, watched))

            def get_kodi_link(self, media_type, local_id):
                return {"kodi_episode_id": 77}

        class KodiDb:
            def set_episode_watched(self, kodi_id, watched):
                events.append(("kodi", kodi_id, watched))

        MediatorService(Store(), KodiDb()).set_watch_status(
            "episode", 12, True
        )
        self.assertEqual(
            [("sqlite", "episode", 12, True), ("kodi", 77, True)], events
        )


if __name__ == "__main__":
    unittest.main()
