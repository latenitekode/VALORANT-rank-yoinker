import time


class LiveBackbone:
    """Reliability-first live-state helper inspired by STATIXVAL.

    VRY's websocket remains the fast wake-up signal. This class validates suspicious
    downgrades and performs bounded active-match discovery so one early 404 does not
    make VRY wait until the next websocket state transition.
    """

    ACTIVE_STATES = {"PREGAME", "INGAME"}
    VALID_STATES = {"MENUS", "PREGAME", "INGAME"}

    def __init__(self, Requests, presences, log):
        self.Requests = Requests
        self.presences = presences
        self.log = log

    def current_state(self):
        """Read the best current local VALORANT presence state."""
        try:
            rows = self.presences.get_presence()
            return self.presences.get_game_state(rows)
        except Exception as exc:
            self.log(f"live backbone: local presence read failed: {exc}")
            return None

    def _membership(self, state):
        if state == "PREGAME":
            endpoint = f"/pregame/v1/players/{self.Requests.puuid}"
        elif state == "INGAME":
            endpoint = f"/core-game/v1/players/{self.Requests.puuid}"
        else:
            return None
        try:
            row = self.Requests.fetch("glz", endpoint, "get")
        except Exception as exc:
            self.log(f"live backbone: membership probe failed for {state}: {exc}")
            return None
        if not isinstance(row, dict):
            return None
        if row.get("errorCode") == "RESOURCE_NOT_FOUND":
            return None
        return row.get("MatchID") or row.get("matchId") or None

    def confirm_transition(self, previous, candidate):
        """Validate a websocket transition without slowing normal transitions.

        PREGAME/INGAME are accepted immediately. A suspicious active -> MENUS
        downgrade gets local-presence and exact-membership rechecks first.
        """
        previous = str(previous or "").upper()
        candidate = str(candidate or "").upper()

        if candidate == "DISCONNECTED":
            return candidate
        if candidate not in self.VALID_STATES:
            return previous or candidate
        if not previous or candidate == previous:
            return candidate
        if candidate in self.ACTIVE_STATES:
            return candidate
        if previous not in self.ACTIVE_STATES or candidate != "MENUS":
            return candidate

        # A single local MENUS frame should not tear down a confirmed active match.
        # Two short local re-reads are cheap and do not touch the remote API.
        last_local = candidate
        for _ in range(2):
            time.sleep(0.12)
            state = self.current_state()
            if state in self.ACTIVE_STATES:
                self.log(
                    f"live backbone: rejected transient {previous}->MENUS; "
                    f"local presence recovered to {state}"
                )
                return state
            if state in self.VALID_STATES:
                last_local = state

        # If local presence still says MENUS, verify exact active membership a few
        # times. These extra GLZ calls happen only on a suspicious downgrade.
        for attempt in range(3):
            match_id = self._membership(previous)
            if match_id:
                self.log(
                    f"live backbone: rejected transient {previous}->MENUS; "
                    f"membership still active ({match_id})"
                )
                return previous
            if attempt < 2:
                time.sleep(0.18)

        return last_local if last_local in self.VALID_STATES else candidate

    def wait_for_match(self, state, timeout=8.0):
        """Return (match_json, match_id) as soon as the active object is ready.

        Riot can publish PREGAME/INGAME presence a little before the GLZ player/match
        endpoints are ready. VRY previously tried once and could then wait for the next
        websocket transition. This bounded loop retries only during that transition.
        """
        state = str(state or "").upper()
        if state == "PREGAME":
            player_ep = f"/pregame/v1/players/{self.Requests.puuid}"
            match_ep = "/pregame/v1/matches/{}"
        elif state == "INGAME":
            player_ep = f"/core-game/v1/players/{self.Requests.puuid}"
            match_ep = "/core-game/v1/matches/{}"
        else:
            return None, None

        deadline = time.monotonic() + max(0.5, float(timeout))
        delay = 0.10
        last_match_id = None
        attempts = 0

        while time.monotonic() < deadline:
            attempts += 1
            try:
                membership = self.Requests.fetch("glz", player_ep, "get")
            except Exception:
                membership = None

            match_id = None
            if isinstance(membership, dict) and membership.get("errorCode") != "RESOURCE_NOT_FOUND":
                match_id = membership.get("MatchID") or membership.get("matchId")

            if match_id:
                last_match_id = match_id
                try:
                    match = self.Requests.fetch("glz", match_ep.format(match_id), "get")
                except Exception:
                    match = None
                if isinstance(match, dict) and match.get("errorCode") != "RESOURCE_NOT_FOUND":
                    self.log(
                        f"live backbone: {state} match ready after {attempts} probe(s): {match_id}"
                    )
                    return match, match_id

            # If presence has already moved elsewhere, stop spinning. We only abort
            # when a recognized different phase is visible; missing presence is treated
            # as uncertainty and the bounded retry continues.
            live_state = self.current_state()
            if live_state in self.VALID_STATES and live_state != state:
                break

            time.sleep(delay)
            delay = min(0.45, delay * 1.45)

        self.log(
            f"live backbone: {state} details unavailable after {attempts} probe(s)"
            + (f"; last match id {last_match_id}" if last_match_id else "")
        )
        return None, last_match_id
