# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from resources.lib.services.watchlist_provider_writer import WatchlistProviderWriter


class Accounts:
    def get_credentials(self,user_id,provider):
        return {"access_token":"token","external_user_id":"1"}


class RecordingWriter(WatchlistProviderWriter):
    def __init__(self):
        super().__init__(Accounts(),simkl_client_id="client")
        self.posts=[]

    def _simkl_post(self,account,path,body):
        self.posts.append((path,body))
        return {}


class WatchlistProviderWriterTests(unittest.TestCase):
    def test_simkl_progress_increase_is_written_as_explicit_episodes(self):
        writer=RecordingWriter()
        result=writer.push("simkl",{
            "simkl_id":"400","status":"CURRENT","progress":3,
        },{"progress":1})

        self.assertEqual("/sync/history",writer.posts[0][0])
        self.assertEqual([{"number":2},{"number":3}],
                         writer.posts[0][1]["shows"][0]["episodes"])
        self.assertEqual("/sync/add-to-list",writer.posts[1][0])
        self.assertEqual(3,result["progress"])

    def test_simkl_progress_decrease_removes_explicit_episodes(self):
        writer=RecordingWriter()
        writer.push("simkl",{
            "simkl_id":"400","status":"CURRENT","progress":1,
        },{"progress":3})

        self.assertEqual("/sync/history/remove",writer.posts[0][0])
        self.assertEqual([{"number":2},{"number":3}],
                         writer.posts[0][1]["shows"][0]["episodes"])


if __name__=="__main__":
    unittest.main()
