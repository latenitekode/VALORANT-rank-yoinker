class Names:
    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log

    @staticmethod
    def _display_name(row):
        if not isinstance(row, dict):
            return "#"
        game_name = row.get("GameName", "")
        tag_line = row.get("TagLine", "")
        return f"{game_name}#{tag_line}" if game_name or tag_line else "#"

    def get_name_from_puuid(self, puuid):
        try:
            response = self.Requests.fetch(
                "pd",
                "/name-service/v2/players",
                "put",
                json_body=[puuid],
            )
            if response is not None and response.ok:
                data = response.json()
                if isinstance(data, list) and data:
                    return self._display_name(data[0])
        except Exception as exc:
            self.log(f"name lookup failed for {puuid}: {exc}")
        return "#"

    def get_multiple_names_from_puuid(self, puuids):
        puuids = list(dict.fromkeys(x for x in puuids if x))
        fallback = {puuid: "#" for puuid in puuids}
        if not puuids:
            return fallback
        try:
            response = self.Requests.fetch(
                "pd",
                "/name-service/v2/players",
                "put",
                json_body=puuids,
            )
            if response is None or not response.ok:
                return fallback
            data = response.json()
            if isinstance(data, list):
                for player in data:
                    subject = player.get("Subject")
                    if subject:
                        fallback[subject] = self._display_name(player)
        except Exception as exc:
            # Names are useful but not authoritative for whether the live scoreboard
            # exists. Keep the table moving and let streamer/agent fallback text work.
            self.log(f"batch name lookup failed: {exc}")
        return fallback

    def get_names_from_puuids(self, players):
        return self.get_multiple_names_from_puuid(
            [player["Subject"] for player in players if player.get("Subject")]
        )

    def get_players_puuid(self, Players):
        return [player["Subject"] for player in Players if player.get("Subject")]
