import base64
import json
import time


class Presences:
    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log

    def get_presence(self):
        presences = self.Requests.fetch(url_type="local", endpoint="/chat/v4/presences", method="get")
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
            if presence.get("championId") is not None or presence.get("product") == "league_of_legends":
                continue
            product = str(presence.get("product") or "").lower()
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
        # Riot can briefly expose duplicate VALORANT presence rows. Prefer a row
        # containing a recognized loop state over a newer but incomplete duplicate.
        candidates.sort(
            key=lambda row: (int(row["valid_loop"]), row["time"]),
            reverse=True,
        )
        return candidates

    def get_game_state(self, presences):
        private_presence = self.get_private_presence(presences)
        if private_presence:
            state = self._loop_from_private(private_presence)
            if state:
                return str(state).upper()
            self.log("ERROR: Unknown presence API structure in 'get_game_state'.")
        return None

    def get_private_presence(self, presences):
        candidates = self._self_presence_candidates(presences)
        return candidates[0]["private"] if candidates else None

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
        """Briefly wait for player presence rows without blocking once per missing player.

        The old implementation slept one full second for *each* absent PUUID and then
        returned anyway. That could add several seconds to the first INGAME render.
        Names/ranks do not require every chat presence row, so this is best-effort only.
        """
        wanted = {str(x) for x in (PlayersPuuids or []) if x}
        if not wanted:
            return []
        deadline = time.monotonic() + max(0.0, float(timeout))
        last = []
        while True:
            last = self.get_presence() or []
            seen = {str(p.get("puuid")) for p in last if p.get("puuid")}
            if wanted.issubset(seen) or time.monotonic() >= deadline:
                return last
            time.sleep(max(0.02, min(float(poll_interval), deadline - time.monotonic())))
