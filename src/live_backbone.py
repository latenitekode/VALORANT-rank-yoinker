import time


class LiveBackbone:
    """Reliability-first live-state helper inspired by STATIXVAL.

    The websocket remains the fast wake-up signal, while local presence and exact
    GLZ membership are used to validate suspicious transitions. Positive live state
    is intentionally sticky during uncertainty: a timeout/429/network failure is not
    evidence that a match ended.
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

    def _membership_status(self, state):
        """Return (status, match_id) for the exact phase membership endpoint.

        status is one of:
          * active   - membership returned a match id
          * inactive - Riot explicitly answered 400/404 / RESOURCE_NOT_FOUND
          * unknown  - timeout, auth/rate-limit/server problem, malformed reply, etc.

        Keeping inactive and unknown separate is crucial: uncertainty must not tear
        down a confirmed live match.
        """
        state = str(state or "").upper()
        if state == "PREGAME":
            endpoint = f"/pregame/v1/players/{self.Requests.puuid}"
        elif state == "INGAME":
            endpoint = f"/core-game/v1/players/{self.Requests.puuid}"
        else:
            return "unknown", None

        try:
            row = self.Requests.fetch("glz", endpoint, "get")
        except Exception as exc:
            self.log(f"live backbone: membership probe failed for {state}: {exc}")
            return "unknown", None

        if not isinstance(row, dict):
            return "unknown", None

        match_id = row.get("MatchID") or row.get("matchId")
        if match_id:
            return "active", match_id

        http_status = row.get("_http_status")
        error_code = str(row.get("errorCode") or "").upper()
        if http_status in (400, 404) or error_code in {
            "RESOURCE_NOT_FOUND",
            "NOT_FOUND",
        }:
            return "inactive", None

        return "unknown", None

    def _probe_previous_active_state(self, previous, attempts=3):
        inactive_votes = 0
        unknown_votes = 0
        for attempt in range(attempts):
            status, match_id = self._membership_status(previous)
            if status == "active":
                return "active", match_id, inactive_votes, unknown_votes
            if status == "inactive":
                inactive_votes += 1
            else:
                unknown_votes += 1
            if attempt < attempts - 1:
                time.sleep(0.18)
        return "inactive" if inactive_votes >= 2 else "unknown", None, inactive_votes, unknown_votes

    def confirm_transition(self, previous, candidate):
        """Validate a websocket transition while keeping real handoffs fast."""
        previous = str(previous or "").upper()
        candidate = str(candidate or "").upper()

        if candidate == "DISCONNECTED":
            return candidate
        if candidate not in self.VALID_STATES:
            return previous or candidate
        if not previous or candidate == previous:
            return candidate

        # The normal fast path: MENUS -> PREGAME and PREGAME -> INGAME should not
        # wait on extra remote calls.
        if candidate in self.ACTIVE_STATES and not (
            previous == "INGAME" and candidate == "PREGAME"
        ):
            return candidate

        # INGAME -> PREGAME is usually a duplicate/flickering presence row rather
        # than a real backwards transition. Keep INGAME unless its exact membership
        # is explicitly gone; then allow the newly-observed PREGAME state.
        if previous == "INGAME" and candidate == "PREGAME":
            local_state = self.current_state()
            if local_state == "INGAME":
                return "INGAME"
            status, match_id = self._membership_status("INGAME")
            if status == "active":
                self.log(
                    "live backbone: rejected transient INGAME->PREGAME; "
                    f"core membership still active ({match_id})"
                )
                return "INGAME"
            if status == "unknown":
                self.log(
                    "live backbone: held INGAME during uncertain reverse transition"
                )
                return "INGAME"
            return "PREGAME"

        if previous not in self.ACTIVE_STATES or candidate != "MENUS":
            return candidate

        # One local MENUS frame must never end a confirmed live match. Re-read twice
        # first because this is cheap and avoids remote traffic for a duplicate row.
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

        # Only explicit repeated absence can clear positive live state. A single 404
        # followed by a timeout/429 is treated as uncertainty, not as match end.
        status, match_id, inactive_votes, unknown_votes = self._probe_previous_active_state(previous)
        if status == "active":
            self.log(
                f"live backbone: rejected transient {previous}->MENUS; "
                f"membership still active ({match_id})"
            )
            return previous
        if status == "unknown":
            self.log(
                f"live backbone: held {previous} during uncertain downgrade "
                f"(inactive={inactive_votes}, unknown={unknown_votes})"
            )
            return previous

        return last_local if last_local in self.VALID_STATES else candidate

    def wait_for_match(self, state, timeout=8.0):
        """Return (match_json, match_id) as soon as the active object is ready.

        Presence can lead GLZ by a short window. Retry only during that bounded
        transition. Match-detail data alone is never used to resurrect a match: an
        exact player-membership match id must be obtained first on every attempt.
        """
        state = str(state or "").upper()
        if state == "PREGAME":
            match_ep = "/pregame/v1/matches/{}"
        elif state == "INGAME":
            match_ep = "/core-game/v1/matches/{}"
        else:
            return None, None

        deadline = time.monotonic() + max(0.5, float(timeout))
        delay = 0.10
        last_match_id = None
        attempts = 0
        explicit_inactive = 0

        while time.monotonic() < deadline:
            attempts += 1
            membership_status, match_id = self._membership_status(state)

            if membership_status == "active" and match_id:
                explicit_inactive = 0
                last_match_id = match_id
                try:
                    match = self.Requests.fetch("glz", match_ep.format(match_id), "get")
                except Exception:
                    match = None
                if isinstance(match, dict):
                    match_error = str(match.get("errorCode") or "").upper()
                    http_status = match.get("_http_status")
                    if not match_error and http_status not in (400, 404):
                        self.log(
                            f"live backbone: {state} match ready after {attempts} probe(s): {match_id}"
                        )
                        return match, match_id
            elif membership_status == "inactive":
                explicit_inactive += 1
            else:
                explicit_inactive = 0

            # A different active phase is a decisive handoff. MENUS is weaker: only
            # stop once exact membership has also been explicitly absent twice.
            live_state = self.current_state()
            if live_state in self.ACTIVE_STATES and live_state != state:
                break
            if live_state == "MENUS" and explicit_inactive >= 2:
                break

            time.sleep(delay)
            delay = min(0.45, delay * 1.45)

        self.log(
            f"live backbone: {state} details unavailable after {attempts} probe(s)"
            + (f"; last match id {last_match_id}" if last_match_id else "")
        )
        return None, last_match_id
