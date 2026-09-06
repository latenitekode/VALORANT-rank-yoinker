import base64
import json
import unittest
from unittest.mock import patch

from src.live_backbone import LiveBackbone
from src.presences import Presences
from src.requestsV import Requests


class FakePresences:
    def __init__(self, states):
        self.states = list(states)
        self.last = self.states[-1] if self.states else None

    def get_presence(self):
        return []

    def get_game_state(self, rows):
        if self.states:
            self.last = self.states.pop(0)
        return self.last


class FakeRequests:
    def __init__(self, replies):
        self.puuid = "self-puuid"
        self.replies = {key: list(value) for key, value in replies.items()}
        self.calls = []

    def fetch(self, url_type, endpoint, method):
        self.calls.append((url_type, endpoint, method))
        for key, values in self.replies.items():
            if key in endpoint:
                if not values:
                    return {"errorCode": "REQUEST_FAILED", "_http_status": 599}
                value = values.pop(0)
                if isinstance(value, Exception):
                    raise value
                return value
        return {"errorCode": "REQUEST_FAILED", "_http_status": 599}


class LiveBackboneTests(unittest.TestCase):
    def setUp(self):
        self.logs = []

    @patch("src.live_backbone.time.sleep", return_value=None)
    def test_single_404_does_not_end_confirmed_match(self, _sleep):
        req = FakeRequests(
            {
                "/core-game/v1/players/": [
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                    {"errorCode": "REQUEST_FAILED", "_http_status": 599},
                    {"MatchID": "live-match"},
                ]
            }
        )
        pres = FakePresences(["MENUS", "MENUS"])
        backbone = LiveBackbone(req, pres, self.logs.append)
        self.assertEqual(backbone.confirm_transition("INGAME", "MENUS"), "INGAME")

    @patch("src.live_backbone.time.sleep", return_value=None)
    def test_repeated_explicit_absence_allows_menus(self, _sleep):
        req = FakeRequests(
            {
                "/core-game/v1/players/": [
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                ]
            }
        )
        pres = FakePresences(["MENUS", "MENUS"])
        backbone = LiveBackbone(req, pres, self.logs.append)
        self.assertEqual(backbone.confirm_transition("INGAME", "MENUS"), "MENUS")

    @patch("src.live_backbone.time.sleep", return_value=None)
    def test_unknown_membership_keeps_positive_state(self, _sleep):
        req = FakeRequests(
            {
                "/pregame/v1/players/": [
                    {"errorCode": "RATE_LIMITED", "_http_status": 429},
                    {"errorCode": "REQUEST_FAILED", "_http_status": 599},
                    {"errorCode": "SERVER_ERROR", "_http_status": 503},
                ]
            }
        )
        pres = FakePresences(["MENUS", "MENUS"])
        backbone = LiveBackbone(req, pres, self.logs.append)
        self.assertEqual(backbone.confirm_transition("PREGAME", "MENUS"), "PREGAME")

    @patch("src.live_backbone.time.sleep", return_value=None)
    def test_match_discovery_retries_early_404_then_succeeds(self, _sleep):
        req = FakeRequests(
            {
                "/pregame/v1/players/": [
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                    {"MatchID": "match-1"},
                    {"MatchID": "match-1"},
                ],
                "/pregame/v1/matches/": [
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                    {"ID": "match-1", "AllyTeam": {"Players": []}},
                ],
            }
        )
        pres = FakePresences(["PREGAME", "PREGAME", "PREGAME"])
        backbone = LiveBackbone(req, pres, self.logs.append)
        match, match_id = backbone.wait_for_match("PREGAME", timeout=1.0)
        self.assertEqual(match_id, "match-1")
        self.assertEqual(match["ID"], "match-1")

    @patch("src.live_backbone.time.sleep", return_value=None)
    def test_stale_match_detail_is_never_used_without_membership(self, _sleep):
        req = FakeRequests(
            {
                "/core-game/v1/players/": [
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                    {"errorCode": "RESOURCE_NOT_FOUND", "_http_status": 404},
                ],
                "/core-game/v1/matches/": [{"ID": "stale-match"}],
            }
        )
        pres = FakePresences(["MENUS", "MENUS"])
        backbone = LiveBackbone(req, pres, self.logs.append)
        match, match_id = backbone.wait_for_match("INGAME", timeout=0.5)
        self.assertIsNone(match)
        self.assertIsNone(match_id)
        self.assertFalse(any("/core-game/v1/matches/" in call[1] for call in req.calls))

    @patch("src.live_backbone.time.sleep", return_value=None)
    def test_reverse_ingame_to_pregame_flicker_is_rejected(self, _sleep):
        req = FakeRequests({"/core-game/v1/players/": [{"MatchID": "core-live"}]})
        pres = FakePresences(["PREGAME"])
        backbone = LiveBackbone(req, pres, self.logs.append)
        self.assertEqual(backbone.confirm_transition("INGAME", "PREGAME"), "INGAME")


class PresenceSelectionTests(unittest.TestCase):
    @staticmethod
    def encoded(payload):
        return base64.b64encode(json.dumps(payload).encode()).decode()

    def test_duplicate_presence_prefers_valid_valorant_loop(self):
        class Req:
            puuid = "self"

        pres = Presences(Req(), lambda _msg: None)
        rows = [
            {
                "puuid": "self",
                "product": "valorant",
                "time": 300,
                "private": self.encoded({"queueId": "competitive"}),
            },
            {
                "puuid": "self",
                "product": "valorant",
                "time": 200,
                "private": self.encoded(
                    {"matchPresenceData": {"sessionLoopState": "INGAME"}}
                ),
            },
            {
                "puuid": "self",
                "product": "league_of_legends",
                "championId": 1,
                "time": 400,
                "private": self.encoded({"sessionLoopState": "MENUS"}),
            },
        ]
        self.assertEqual(pres.get_game_state(rows), "INGAME")

    def test_cached_private_presence_is_not_state_authority(self):
        class Req:
            puuid = "self"

        pres = Presences(Req(), lambda _msg: None)
        rows = [
            {
                "puuid": "self",
                "product": "valorant",
                "private": self.encoded({"sessionLoopState": "PREGAME", "queueId": "competitive"}),
            }
        ]
        self.assertEqual(pres.get_private_presence(rows)["queueId"], "competitive")
        self.assertIsNotNone(pres.get_cached_private_presence())
        self.assertIsNone(pres.get_game_state([]))


class RequestHelperTests(unittest.TestCase):
    def test_version_tuple_handles_v_prefix_and_patch(self):
        self.assertGreater(Requests._version_tuple("v1.12.3"), Requests._version_tuple("1.11"))


if __name__ == "__main__":
    unittest.main()
