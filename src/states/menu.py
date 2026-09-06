class Menu:
    def __init__(self, Requests, log, presences):
        self.Requests = Requests
        self.log = log
        self.presences = presences

    @staticmethod
    def _party_fields(decoded_presence):
        if not isinstance(decoded_presence, dict):
            return "", 0, 0
        party = decoded_presence.get("partyPresenceData")
        player = decoded_presence.get("playerPresenceData")
        if isinstance(party, dict):
            return (
                party.get("partyId", ""),
                party.get("partySize", 0) or 0,
                (player or {}).get("accountLevel", 0) if isinstance(player, dict) else 0,
            )
        return (
            decoded_presence.get("partyId", ""),
            decoded_presence.get("partySize", 0) or 0,
            decoded_presence.get("accountLevel", 0) or 0,
        )

    def get_party_json(self, GamePlayersPuuid, presencesDICT):
        party_json = {}
        game_players = set(GamePlayersPuuid or [])
        for presence in presencesDICT or []:
            if presence.get("puuid") not in game_players:
                continue
            decoded = self.presences.decode_presence(presence.get("private"))
            if not decoded.get("isValid"):
                continue
            party_id, party_size, _ = self._party_fields(decoded)
            if party_id and party_size > 1:
                party_json.setdefault(party_id, []).append(presence.get("puuid"))

        # Remove parties for which only one in-match player is currently visible.
        party_json = {
            party_id: members
            for party_id, members in party_json.items()
            if len(members) > 1
        }
        self.log(f"retrieved party json: {party_json}")
        return party_json

    def get_party_members(self, self_puuid, presencesDICT):
        rows = list(presencesDICT or [])
        result = []
        own_party_id = ""

        for presence in rows:
            if presence.get("puuid") != self_puuid:
                continue
            decoded = self.presences.decode_presence(presence.get("private"))
            if not decoded.get("isValid"):
                continue
            own_party_id, _, account_level = self._party_fields(decoded)
            result.append(
                {
                    "Subject": presence.get("puuid"),
                    "PlayerIdentity": {"AccountLevel": account_level},
                }
            )
            break

        if own_party_id:
            for presence in rows:
                puuid = presence.get("puuid")
                if not puuid or puuid == self_puuid:
                    continue
                decoded = self.presences.decode_presence(presence.get("private"))
                if not decoded.get("isValid"):
                    continue
                party_id, _, account_level = self._party_fields(decoded)
                if party_id == own_party_id:
                    result.append(
                        {
                            "Subject": puuid,
                            "PlayerIdentity": {"AccountLevel": account_level},
                        }
                    )

        self.log(f"retrieved party members: {result}")
        return result
