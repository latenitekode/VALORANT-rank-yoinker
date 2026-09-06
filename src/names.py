import requests


class Names:

    def __init__(self, Requests, log):
        self.Requests = Requests
        self.log = log

    def get_name_from_puuid(self, puuid):
        try:
            response = requests.put(
                self.Requests.pd_url + "/name-service/v2/players",
                headers=self.Requests.get_headers(),
                json=[puuid],
                verify=False,
                timeout=(2.5, 6.0),
            )
            data = response.json()
            if data and isinstance(data, list):
                return data[0].get("GameName", "") + "#" + data[0].get("TagLine", "")
        except Exception as exc:
            self.log(f"name lookup failed for {puuid}: {exc}")
        return "#"

    def get_multiple_names_from_puuid(self, puuids):
        puuids = [x for x in puuids if x]
        fallback = {puuid: "#" for puuid in puuids}
        if not puuids:
            return fallback
        try:
            response = requests.put(
                self.Requests.pd_url + "/name-service/v2/players",
                headers=self.Requests.get_headers(),
                json=puuids,
                verify=False,
                timeout=(2.5, 6.0),
            )
            data = response.json()
            if isinstance(data, dict) and data.get("errorCode"):
                self.log(f'{data.get("errorCode")}, new token retrieved')
                response = requests.put(
                    self.Requests.pd_url + "/name-service/v2/players",
                    headers=self.Requests.get_headers(refresh=True),
                    json=puuids,
                    verify=False,
                    timeout=(2.5, 6.0),
                )
                data = response.json()
            if isinstance(data, list):
                for player in data:
                    subject = player.get("Subject")
                    if subject:
                        fallback[subject] = f"{player.get('GameName', '')}#{player.get('TagLine', '')}"
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
