import threading
import time
import unittest

from src.player_stats import PlayerStats
from src.rank import Rank


class FakeResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload or {}
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = str(self._payload)

    def json(self):
        return self._payload


class RuntimeCacheTests(unittest.TestCase):
    def test_rank_cache_reuses_one_response(self):
        class Req:
            calls = 0

            def fetch(self, *_args):
                self.calls += 1
                return FakeResponse({"QueueSkills": {}})

        req = Req()
        rank = Rank(req, lambda _msg: None, None, [])
        first = rank.get_request("p1")
        second = rank.get_request("p1")
        self.assertIs(first, second)
        self.assertEqual(req.calls, 1)

    def test_match_details_are_single_flight(self):
        class Req:
            def __init__(self):
                self.calls = 0
                self.lock = threading.Lock()

            def fetch(self, *_args):
                with self.lock:
                    self.calls += 1
                time.sleep(0.03)
                return FakeResponse({"players": [], "roundResults": []})

        class Config:
            def get_table_flag(self, _flag):
                return True

        req = Req()
        stats = PlayerStats(req, lambda _msg: None, Config())
        results = []

        def worker():
            results.append(stats._get_match_details_cached("m1"))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(req.calls, 1)
        self.assertEqual(len(results), 4)


if __name__ == "__main__":
    unittest.main()
