# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import unittest
from urllib.parse import parse_qs

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


class Response:
    def __init__(self,payload):
        self.payload=json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self,*args):
        return False

    def read(self):
        return self.payload


class WatchlistProviderWriterTests(unittest.TestCase):
    def test_mal_progress_uses_documented_put_operation(self):
        requests=[]

        def open_request(request,timeout):
            requests.append((request,timeout))
            return Response({"status":"watching","num_episodes_watched":4})

        writer=WatchlistProviderWriter(Accounts(),opener=open_request)
        result=writer.push("mal",{
            "mal_id":"21","status":"CURRENT","progress":4,
        })

        request,timeout=requests[0]
        self.assertEqual("PUT",request.get_method())
        self.assertEqual(
            "https://api.myanimelist.net/v2/anime/21/my_list_status",
            request.full_url)
        self.assertEqual({"status":["watching"],"num_watched_episodes":["4"]},
                         parse_qs(request.data.decode("utf-8")))
        self.assertEqual("Bearer token",request.get_header("Authorization"))
        self.assertEqual(20,timeout)
        self.assertEqual(4,result["progress"])

    def test_mal_writes_are_paced(self):
        now=[10.0]
        sleeps=[]

        def monotonic():
            return now[0]

        def sleep(delay):
            sleeps.append(delay)
            now[0]+=delay

        def open_request(request,timeout):
            return Response({"num_episodes_watched":1})

        writer=WatchlistProviderWriter(
            Accounts(),opener=open_request,monotonic=monotonic,sleeper=sleep)
        item={"mal_id":"21","status":"CURRENT","progress":1}
        writer.push("mal",item)
        writer.push("mal",item)

        self.assertEqual(1,len(sleeps))
        self.assertAlmostEqual(1.1,sleeps[0])

    def test_kitsu_completed_progress_uses_kitsu_episode_count(self):
        target=WatchlistProviderWriter.target_state("kitsu",{
            "status":"COMPLETED","progress":4,
        },{
            "status":"COMPLETED","progress":2,"episode_count":2,
        })

        self.assertEqual({"status":"COMPLETED","progress":2},target)

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
