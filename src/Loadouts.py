import time
import threading
import requests
from src.colors import color
from src.constants import sockets, hide_names


class Loadouts:
    def __init__(self, Requests, log, colors, Server, current_map):

        self.Requests = Requests
        self.log = log
        self.colors = colors
        self.Server = Server
        self.current_map = current_map
        self._api_cache = {}
        self._api_lock = threading.RLock()
        self._api_key_locks = {}
        self._prime_thread = None

    def _api_get(self, key, url):
        with self._api_lock:
            cached = self._api_cache.get(key)
            key_lock = self._api_key_locks.setdefault(key, threading.Lock())
        if cached is not None:
            return cached

        with key_lock:
            with self._api_lock:
                cached = self._api_cache.get(key)
            if cached is not None:
                return cached
            try:
                response = requests.get(url, timeout=(2.0, 5.0))
                response.raise_for_status()
            except Exception as exc:
                self.log(f"loadout metadata fetch failed for {key}: {exc}")
                with self._api_lock:
                    cached = self._api_cache.get(key)
                if cached is not None:
                    return cached
                fallback = requests.Response()
                fallback.status_code = 200
                fallback._content = b'{"data": []}'
                return fallback
            with self._api_lock:
                self._api_cache[key] = response
            return response

    def prime_metadata_async(self):
        """Warm static valorant-api metadata in the background while VRY is in menus."""
        if self._prime_thread is not None and self._prime_thread.is_alive():
            return
        urls = {
            "weapons": "https://valorant-api.com/v1/weapons",
            "skins": "https://valorant-api.com/v1/weapons/skins",
            "sprays": "https://valorant-api.com/v1/sprays",
            "flex": "https://valorant-api.com/v1/flex",
            "buddies": "https://valorant-api.com/v1/buddies",
            "agents": "https://valorant-api.com/v1/agents",
            "titles": "https://valorant-api.com/v1/playertitles",
            "playercards": "https://valorant-api.com/v1/playercards",
        }
        def worker():
            for key, url in urls.items():
                try:
                    self._api_get(key, url)
                except Exception:
                    # Live loadout code can retry on demand later.
                    pass
        self._prime_thread = threading.Thread(target=worker, name="vry-metadata-prime", daemon=True)
        self._prime_thread.start()

    @staticmethod
    def _empty_payload(players, state="game"):
        if state == "pregame" and isinstance(players, dict):
            rows = (players.get("AllyTeam") or {}).get("Players", [])
        else:
            rows = players if isinstance(players, list) else []
        return {
            "Players": {p.get("Subject"): {} for p in rows if p.get("Subject")},
            "time": int(time.time()),
        }

    def get_skin_metadata(self):
        return self._api_get("skins", "https://valorant-api.com/v1/weapons/skins")

    def get_match_loadouts(self, match_id, players, weaponChoose, valoApiSkins, names, state="game"):
        weaponLists = {}
        valApiWeapons = self._api_get(
            "weapons", "https://valorant-api.com/v1/weapons").json()
        if state == "game":
            team_id = "Blue"
            PlayerInventorys = self.Requests.fetch(
                "glz", f"/core-game/v1/matches/{match_id}/loadouts", "get")
        elif state == "pregame":
            pregame_stats = players
            players = players["AllyTeam"]["Players"]
            team_id = pregame_stats['Teams'][0]['TeamID']
            PlayerInventorys = self.Requests.fetch(
                "glz", f"/pregame/v1/matches/{match_id}/loadouts", "get")

        if not isinstance(PlayerInventorys, dict) or not isinstance(PlayerInventorys.get("Loadouts"), list):
            self.log("loadouts unavailable; continuing scoreboard without cosmetic data")
            empty = self._empty_payload(players, state=state)
            self.Server.send_payload("matchLoadout", empty)
            return [weaponLists, empty]

        # subject (player UUID) -> loadout lookup
        loadout_by_subject = {}
        for loadout_entry in PlayerInventorys["Loadouts"]:
            subj = loadout_entry.get("Subject", "").lower()
            # if player has an agent != spectator
            char_id = loadout_entry.get("CharacterID", "")
            if subj and (state == "pregame" or char_id):
                loadout_by_subject[subj] = loadout_entry["Loadout"] if state == "game" else loadout_entry

        for player in players:
            subj = player.get("Subject", "").lower()
            inv = loadout_by_subject.get(subj)
            if inv is None:
                continue
            for weapon in valApiWeapons.get("data", []):
                if str(weapon.get("displayName", "")).lower() != str(weaponChoose).lower():
                    continue
                weapon_item = (inv.get("Items") or {}).get(str(weapon.get("uuid", "")).lower()) or {}
                skin_socket = (weapon_item.get("Sockets") or {}).get("bcef87d6-209b-46c6-8b19-fbe40bd95abc") or {}
                skin_id = str((skin_socket.get("Item") or {}).get("ID") or "")
                if not skin_id:
                    continue
                json_data = valoApiSkins.json()

                if "data" not in json_data:
                    self.log("Skins API response missing 'data'.")
                    break

                for skin in json_data["data"]:
                    if skin_id.lower() == str(skin.get("uuid", "")).lower():
                        rgb_color = self.colors.get_rgb_color_from_skin(
                            skin["uuid"].lower(), valoApiSkins)
                        skin_display_name = skin["displayName"].replace(
                            f" {weapon['displayName']}", "")
                        weaponLists.update({player["Subject"]: color(
                            skin_display_name, fore=rgb_color)})
                        break
        try:
            final_json = self.convertLoadoutToJsonArray(
                PlayerInventorys, players, state, names, team_id=team_id)
        except Exception as exc:
            self.log(f"loadout conversion failed; continuing scoreboard: {exc}")
            final_json = self._empty_payload(players, state=state)
        # self.log(f"json for website: {final_json}")
        self.Server.send_payload("matchLoadout", final_json)
        return [weaponLists, final_json]

    # this will convert valorant loadouts to json with player names
    def convertLoadoutToJsonArray(self, PlayerInventorys, players, state, names, team_id=None):
        # Static valorant-api metadata is cached once and indexed once per conversion.
        # The original implementation repeatedly reparsed large JSON responses inside
        # every player/weapon loop, which added noticeable CPU time at match start.
        sprays_data = self._api_get("sprays", "https://valorant-api.com/v1/sprays").json().get("data", [])
        flex_data = self._api_get("flex", "https://valorant-api.com/v1/flex").json().get("data", [])
        weapons_data = self._api_get("weapons", "https://valorant-api.com/v1/weapons").json().get("data", [])
        buddies_data = self._api_get("buddies", "https://valorant-api.com/v1/buddies").json().get("data", [])
        agents_data = self._api_get("agents", "https://valorant-api.com/v1/agents").json().get("data", [])
        titles_data = self._api_get("titles", "https://valorant-api.com/v1/playertitles").json().get("data", [])
        cards_data = self._api_get("playercards", "https://valorant-api.com/v1/playercards").json().get("data", [])

        sprays_by_uuid = {str(x.get("uuid", "")).lower(): x for x in sprays_data if isinstance(x, dict) and x.get("uuid")}
        flex_by_uuid = {str(x.get("uuid", "")).lower(): x for x in flex_data if isinstance(x, dict) and x.get("uuid")}
        buddies_by_uuid = {str(x.get("uuid", "")).lower(): x for x in buddies_data if isinstance(x, dict) and x.get("uuid")}
        agents_by_uuid = {str(x.get("uuid", "")).lower(): x for x in agents_data if isinstance(x, dict) and x.get("uuid")}
        titles_by_uuid = {str(x.get("uuid", "")).lower(): x for x in titles_data if isinstance(x, dict) and x.get("uuid")}
        cards_by_uuid = {str(x.get("uuid", "")).lower(): x for x in cards_data if isinstance(x, dict) and x.get("uuid")}
        weapons_by_uuid = {str(x.get("uuid", "")).lower(): x for x in weapons_data if isinstance(x, dict) and x.get("uuid")}

        final_final_json = {"Players": {}, "time": int(time.time()), "map": self.current_map}
        final_json = final_final_json["Players"]
        raw_loadouts = PlayerInventorys.get("Loadouts", []) if isinstance(PlayerInventorys, dict) else []

        loadout_by_subject = {}
        for entry in raw_loadouts:
            subj = str(entry.get("Subject", "")).lower()
            char_id = entry.get("CharacterID", "")
            if subj and (state == "pregame" or char_id):
                loadout_by_subject[subj] = entry

        for player in players:
            subject = player.get("Subject")
            if not subject:
                continue
            subj = subject.lower()
            final_json[subject] = {}
            loadout_entry = loadout_by_subject.get(subj)
            if loadout_entry is None:
                continue

            PlayerInventory = loadout_entry.get("Loadout", loadout_entry) or {}
            character_id = str(
                player.get("CharacterID")
                or loadout_entry.get("CharacterID")
                or PlayerInventory.get("CharacterID")
                or ""
            ).lower()
            agent_meta = agents_by_uuid.get(character_id)

            if hide_names:
                if state == "game" and agent_meta:
                    final_json[subject]["Name"] = agent_meta.get("displayName")
            else:
                player_name = names.get(subject) or names.get(subj)
                final_json[subject]["Name"] = player_name if player_name and player_name != "#" else None

            final_json[subject]["Team"] = player.get("TeamID", team_id)
            final_json[subject]["Sprays"] = {}
            identity = player.get("PlayerIdentity", {}) or {}
            final_json[subject]["Level"] = identity.get("AccountLevel")

            title = titles_by_uuid.get(str(identity.get("PlayerTitleID", "")).lower())
            if title:
                final_json[subject]["Title"] = title.get("titleText")
            card = cards_by_uuid.get(str(identity.get("PlayerCardID", "")).lower())
            if card:
                final_json[subject]["PlayerCard"] = card.get("largeArt")
            if agent_meta:
                final_json[subject]["AgentArtworkName"] = str(agent_meta.get("displayName", "")) + "Artwork"
                final_json[subject]["Agent"] = agent_meta.get("displayIcon")

            expression_selections = (PlayerInventory.get("Expressions") or {}).get("AESSelections", [])
            for j, expr in enumerate(expression_selections):
                asset_id = str(expr.get("AssetID") or "").lower()
                if not asset_id:
                    continue
                expression_data = sprays_by_uuid.get(asset_id)
                expression_type = "spray" if expression_data else None
                if expression_data is None:
                    expression_data = flex_by_uuid.get(asset_id)
                    expression_type = "flex" if expression_data else "unknown"
                entry = {"type": expression_type}
                if expression_data:
                    entry.update({
                        "displayName": expression_data.get("displayName", ""),
                        "displayIcon": expression_data.get("displayIcon"),
                        "fullTransparentIcon": expression_data.get("fullTransparentIcon") or expression_data.get("displayIcon"),
                    })
                final_json[subject]["Sprays"][j] = entry

            final_json[subject]["Weapons"] = {}
            items = PlayerInventory.get("Items", {}) or {}
            for weapon_uuid, weapon_item in items.items():
                weapon_key = str(weapon_uuid).lower()
                weapon_output = {}
                final_json[subject]["Weapons"][weapon_uuid] = weapon_output
                item_sockets = (weapon_item or {}).get("Sockets", {}) or {}

                for var_socket, socket_uuid in sockets.items():
                    socket_row = item_sockets.get(socket_uuid) or {}
                    item = socket_row.get("Item") or {}
                    if item.get("ID"):
                        weapon_output[var_socket] = item.get("ID")

                buddy_id = str(weapon_output.get("skin_buddy", "")).lower()
                buddy = buddies_by_uuid.get(buddy_id)
                if buddy:
                    weapon_output["buddy_displayIcon"] = buddy.get("displayIcon")

                weapon_meta = weapons_by_uuid.get(weapon_key)
                if not weapon_meta:
                    continue
                weapon_output["weapon"] = weapon_meta.get("displayName")
                selected_skin_id = str(weapon_output.get("skin", "")).lower()
                selected_chroma_id = str(weapon_output.get("skin_chroma", "")).lower()
                for skin_meta in weapon_meta.get("skins", []) or []:
                    if str(skin_meta.get("uuid", "")).lower() != selected_skin_id:
                        continue
                    weapon_output["skinDisplayName"] = skin_meta.get("displayName")
                    chosen_icon = None
                    for chroma in skin_meta.get("chromas", []) or []:
                        if str(chroma.get("uuid", "")).lower() == selected_chroma_id:
                            chosen_icon = chroma.get("displayIcon") or chroma.get("fullRender")
                            break
                    if not chosen_icon:
                        chosen_icon = skin_meta.get("displayIcon")
                    if not chosen_icon and skin_meta.get("levels"):
                        chosen_icon = (skin_meta.get("levels") or [{}])[0].get("displayIcon")
                    display_name = str(skin_meta.get("displayName") or "")
                    if display_name.startswith("Standard") or display_name.startswith("Melee"):
                        chosen_icon = weapon_meta.get("displayIcon")
                    if chosen_icon:
                        weapon_output["skinDisplayIcon"] = chosen_icon
                    break

        return final_final_json

