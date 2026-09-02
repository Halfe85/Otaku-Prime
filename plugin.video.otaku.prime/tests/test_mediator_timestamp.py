from __future__ import annotations

import io
import json
import unittest

from resources.lib.services.mediator_timestamp import (
    AniSkipTimestampClient,
    MediatorTimestampService,
    TheIntroDBTimestampClient,
)


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.payload


class RecordingOpener:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request.full_url, timeout))
        return FakeResponse(self.payload)


class FakeCatalog:
    def __init__(self):
        self.replacements = []
        self.errors = []

    def replace_episode_segments(self, episode_id, segments, status="FOUND", error=None):
        row = {
            "episode_id": episode_id,
            "segments": list(segments),
            "status": status,
            "error": error,
            "segment_count": len(segments),
        }
        self.replacements.append(row)
        return row

    def record_episode_timestamp_error(self, episode_id, error):
        self.errors.append((episode_id, error))


class FakeAniSkip:
    def __init__(self, segments):
        self.segments = list(segments)
        self.calls = []

    def fetch(self, mal_id, episode_number, episode_length_seconds=0):
        self.calls.append((mal_id, episode_number, episode_length_seconds))
        return list(self.segments)


class FakeIntroDB:
    def __init__(self, segments):
        self.segments = list(segments)
        self.calls = []

    def fetch(self, tvdb_id, season_number, episode_number):
        self.calls.append((tvdb_id, season_number, episode_number))
        return list(self.segments)


class MediatorTimestampTests(unittest.TestCase):
    def test_aniskip_converts_float_seconds_to_milliseconds(self):
        opener = RecordingOpener({
            "found": True,
            "results": [{
                "skipId": "op-1",
                "skipType": "op",
                "episodeLength": 1421.713,
                "interval": {"startTime": 638.489, "endTime": 728.489},
            }, {
                "skipId": "ed-1",
                "skipType": "ed",
                "episodeLength": 1421.713,
                "interval": {"startTime": 1331.713, "endTime": 1421.713},
            }],
        })
        client = AniSkipTimestampClient(timeout=8, opener=opener)

        segments = client.fetch(9253, 1, episode_length_seconds=0)

        self.assertEqual(2, len(segments))
        self.assertEqual("intro", segments[0]["type"])
        self.assertEqual(638489, segments[0]["start_ms"])
        self.assertEqual(728489, segments[0]["end_ms"])
        self.assertEqual(1421713, segments[0]["source_duration_ms"])
        self.assertEqual("credits", segments[1]["type"])
        self.assertIn("types=op", opener.requests[0][0])
        self.assertIn("types=ed", opener.requests[0][0])
        self.assertIn("types=recap", opener.requests[0][0])

    def test_theintrodb_preserves_open_ended_credits(self):
        opener = RecordingOpener({
            "tmdb_id": 1396,
            "type": "tv",
            "intro": [{"start_ms": None, "end_ms": 90000}],
            "credits": [{"start_ms": 1800000, "end_ms": None}],
        })
        client = TheIntroDBTimestampClient(timeout=8, opener=opener)

        segments = client.fetch(81189, 1, 1)

        self.assertEqual(2, len(segments))
        self.assertEqual(0, segments[0]["start_ms"])
        self.assertEqual(90000, segments[0]["end_ms"])
        self.assertEqual(1800000, segments[1]["start_ms"])
        self.assertIsNone(segments[1]["end_ms"])
        self.assertIn("tvdb_id=81189", opener.requests[0][0])
        self.assertIn("season=1", opener.requests[0][0])
        self.assertIn("episode=1", opener.requests[0][0])

    def test_aniskip_result_wins_without_calling_fallback(self):
        catalog = FakeCatalog()
        aniskip = FakeAniSkip([{
            "type": "intro", "start_ms": 1000, "end_ms": 90000,
            "source": "aniskip", "source_duration_ms": 1440000,
            "source_ref": "x",
        }])
        introdb = FakeIntroDB([{
            "type": "credits", "start_ms": 1300000, "end_ms": None,
            "source": "theintrodb", "source_duration_ms": None,
            "source_ref": "1",
        }])
        service = MediatorTimestampService(
            catalog, aniskip=aniskip, theintrodb=introdb, sleep=lambda _: None
        )

        service._enrich_episode({
            "episode_local_id": "abcdef000001000001",
            "timestamp_mal_id": "9253",
            "source_episode_number": 1,
            "runtime_minutes": 24,
            "tvdb_id": "81189",
            "timestamp_season_number": 1,
            "timestamp_episode_number": 1,
            "release_date": "2020-01-01",
        })

        self.assertEqual(1, len(aniskip.calls))
        self.assertEqual([], introdb.calls)
        self.assertEqual("FOUND", catalog.replacements[0]["status"])
        self.assertEqual("aniskip", catalog.replacements[0]["segments"][0]["source"])

    def test_theintrodb_fills_episode_when_aniskip_has_no_segments(self):
        catalog = FakeCatalog()
        aniskip = FakeAniSkip([])
        introdb = FakeIntroDB([{
            "type": "credits", "start_ms": 1300000, "end_ms": None,
            "source": "theintrodb", "source_duration_ms": None,
            "source_ref": "1396",
        }])
        service = MediatorTimestampService(
            catalog, aniskip=aniskip, theintrodb=introdb, sleep=lambda _: None
        )

        service._enrich_episode({
            "episode_local_id": "abcdef000001000001",
            "timestamp_mal_id": "9253",
            "source_episode_number": 1,
            "runtime_minutes": None,
            "tvdb_id": "81189",
            "timestamp_season_number": 1,
            "timestamp_episode_number": 1,
            "release_date": "2020-01-01",
        })

        self.assertEqual(1, len(aniskip.calls))
        self.assertEqual([("81189", 1, 1)], introdb.calls)
        self.assertEqual("FOUND", catalog.replacements[0]["status"])
        self.assertEqual("theintrodb", catalog.replacements[0]["segments"][0]["source"])


if __name__ == "__main__":
    unittest.main()
