import base64
import json
import threading
import time


class Presences:
    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log
        self._cache_lock = threading.RLock()
        self._last_private_presence = None
        self._last_private_presence_at = 0.0

    def get_presence(self):
        presences = self.Requests.fetch(
            url_type="local", endpoint="/chat/v4/presences", method="get"
        )
        if presences is None:
            return None
        return presences.get("presences", [])

    @staticmethod
    def _decode_private_value(value):
        if value in (None, ""):
            return None
        try:
            raw = base64.b64decode(str(value))
            return json.loads(raw)
        except (ValueError, TypeError, json.JSONDecodeError):
            return None

    @staticmethod
    def _loop_from_private(private_presence):
        if not isinstance(private_presence, dict):
            return None
        nested = private_presence.get("matchPresenceData")
        if isinstance(nested, dict) and nested.get("sessionLoopState"):
            return nested.get("sessionLoopState")
        return private_presence.get("sessionLoopState")

    def _self_presence_candidates(self, presences):
        candidates = []
        for presence in presences or []:
            if presence.get("puuid") != self.Requests.puuid:
                continue

            # The Riot chat endpoint can expose the same PUUID for multiple products.
            # Explicitly reject LoL-shaped rows before looking at VALORANT private data.
            if presence.get("championId") is not None:
                continue
            product = str(presence.get("product") or "").lower()
            if product in {"league_of_legends", "lol"}:
                continue
            if product and product != "valorant":
                continue

            private_presence = self._decode_private_value(presence.get("private"))
            if private_presence is None:
                continue
            loop = str(self._loop_from_private(private_presence) or "").upper()
            candidates.append(
                {
                    "presence": presence,
                    "private": private_presence,
                    "loop": loop,
                    "valid_loop": loop in ("MENUS", "PREGAME", "INGAME"),
                    "time": int(presence.get("time") or 0),
                }
            )

        # Duplicate VALORANT rows can briefly disagree. Prefer a recognized loop over
        # a newer but incomplete duplicate, then prefer the newest candidate.
        candidates.sort(
            key=lambda row: (int(row["valid_loop"]), row["time"]), reverse=True
        )
        return candidates

    def get_game_state(self, presences):
        # State authority is always current data, never the cached fallback below.
        candidates = self._self_presence_candidates(presences)
        if candidates:
            state = self._loop_from_private(candidates[0]["private"])
            if state:
                return str(state).upper()
            self.log("ERROR: Unknown presence API structure in 'get_game_state'.")
        return None

    def get_private_presence(self, presences):
        candidates = self._self_presence_candidates(presences)
        if not candidates:
            return None
        private_presence = candidates[0]["private"]
        with self._cache_lock:
            self._last_private_presence = dict(private_presence)
            self._last_private_presence_at = time.monotonic()
        return private_presence

    def get_cached_private_presence(self, max_age=8.0):
        """Return a recent last-good private presence for optional UI metadata only."""
        with self._cache_lock:
            if self._last_private_presence is None:
                return None
            if time.monotonic() - self._last_private_presence_at > max(0.0, float(max_age)):
                return None
            return dict(self._last_private_presence)

    def decode_presence(self, private):
        if "{" not in str(private) and private is not None and str(private) != "":
            decoded_party_presence = self._decode_private_value(private)
            if isinstance(decoded_party_presence, dict) and decoded_party_presence.get("isValid"):
                return decoded_party_presence
        return {
            "isValid": False,
            "partyId": 0,
            "partySize": 0,
            "partyVersion": 0,
        }

    def wait_for_presence(self, PlayersPuuids, timeout=0.8, poll_interval=0.12):
        """Briefly wait for player rows as one batch, never one long sleep per PUUID."""
        wanted = {x for x in PlayersPuuids or [] if x}
        result = {puuid: None for puuid in wanted}
        if not wanted:
            return result

        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            current = self.get_presence() or []
            for presence in current:
                puuid = presence.get("puuid")
                if puuid in wanted and result.get(puuid) is None:
                    result[puuid] = presence

            if all(value is not None for value in result.values()):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(min(float(poll_interval), max(0.0, deadline - time.monotonic())))

        return result
