import re
import websockets
import websockets.client
import ssl
import base64
import json
import asyncio
from src.colors import color

class Ws:
    def __init__(self, lockfile, Requests, cfg, colors, hide_names, server, rpc=None):
        self.lockfile = lockfile
        self.Requests = Requests
        self.log = Requests.log  # Inherit logger from Requests
        self.ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.id_seen = []
        self.cfg = cfg
        self.player_data = {}
        self.messages = 0
        self.colors = colors
        self.hide_names = hide_names
        self.message_history = []
        self.up = "\033[A"
        self.chat_limit = cfg.chat_limit
        self.server = server
        if self.cfg.get_feature_flag("discord_rpc"):
            self.rpc = rpc

    def set_player_data(self, player_data):
        self.player_data = player_data

    def _state_from_presence_rows(self, rows):
        candidates = []
        for presence in rows or []:
            if presence.get("puuid") != self.Requests.puuid:
                continue
            if presence.get("championId") is not None:
                continue
            product = str(presence.get("product") or "").lower()
            if product in {"league_of_legends", "lol"} or (product and product != "valorant"):
                continue
            try:
                private_data = json.loads(base64.b64decode(presence.get("private", "")))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
            state = None
            if isinstance(private_data.get("matchPresenceData"), dict):
                state = private_data.get("matchPresenceData", {}).get("sessionLoopState")
            if not state:
                state = private_data.get("sessionLoopState")
            state = str(state or "").upper()
            candidates.append((
                state in ("MENUS", "PREGAME", "INGAME"),
                int(presence.get("time") or 0),
                state,
                private_data,
            ))
        if not candidates:
            return None, None
        candidates.sort(key=lambda row: (int(row[0]), row[1]), reverse=True)
        _, _, state, private_data = candidates[0]
        return state or None, private_data

    async def recconect_to_websocket(self, initial_game_state):
        local_headers = {
            'Authorization': 'Basic ' + base64.b64encode(('riot:' + self.lockfile['password']).encode()).decode()
        }
        url = f"wss://127.0.0.1:{self.lockfile['port']}"
        
        max_retries = 5
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                async with websockets.connect(
                    url,
                    ssl=self.ssl_context,
                    extra_headers=local_headers,
                    open_timeout=5,
                    close_timeout=2,
                    ping_interval=20,
                    ping_timeout=10,
                ) as websocket:
                    await websocket.send('[5, "OnJsonApiEvent_chat_v4_presences"]')
                    if self.cfg.get_feature_flag("game_chat"):
                        await websocket.send('[5, "OnJsonApiEvent_chat_v6_messages"]')
                    
                    while True:
                        try:
                            response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                            result = self.handle(response, initial_game_state)
                            if result is not None:
                                return result
                        except asyncio.TimeoutError:
                            # Websocket is the fast wake-up signal, not the only source of
                            # truth. If Riot drops/coalesces an event, poll the local client
                            # presence once per second so VRY cannot sit in the old state for
                            # half a round waiting for another event. This is local-only.
                            try:
                                payload = await asyncio.to_thread(
                                    self.Requests.fetch,
                                    "local",
                                    "/chat/v4/presences",
                                    "get",
                                )
                                state, private_data = self._state_from_presence_rows(
                                    (payload or {}).get("presences", [])
                                )
                                if state and state != initial_game_state:
                                    if self.cfg.get_feature_flag("discord_rpc") and private_data:
                                        self.rpc.set_rpc(private_data)
                                    self.messages = 0
                                    self.message_history = []
                                    return state
                            except Exception as e:
                                self.log(f"Local presence fallback failed: {e}")
            except (websockets.exceptions.ConnectionClosed, websockets.exceptions.InvalidURI, websockets.exceptions.InvalidHandshake, ConnectionRefusedError, OSError) as e:
                self.log(f"Websocket failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2
                else:
                    self.log(f"Websocket failed after {max_retries} attempts.")
                    return "DISCONNECTED"
            except Exception as e:
                self.log(f"Unexpected websocket error: {e}")
                return "DISCONNECTED"
        
        return "DISCONNECTED"

    def handle(self, m, initial_game_state):
        try:
            if not m or len(m) <= 10:
                return None
            resp_json = json.loads(m)
        except (json.JSONDecodeError, TypeError):
            self.log(f"JSONDecodeError: Failed to parse websocket message. Data: {m}")
            return None

        if resp_json[2].get("uri") == "/chat/v4/presences":
            rows = resp_json[2].get("data", {}).get("presences", []) or []
            state, private_data = self._state_from_presence_rows(rows)
            if state:
                if self.cfg.get_feature_flag("discord_rpc") and private_data:
                    self.rpc.set_rpc(private_data)
                if state != initial_game_state:
                    self.messages = 0
                    self.message_history = []
                    return state

        elif resp_json[2].get("uri") == "/chat/v6/messages":
            message = resp_json[2].get("data", {}).get("messages", [{}])[0]
            if "ares-coregame" in message.get("cid", "") and message.get("id") not in self.id_seen:
                
                self.ally_team = self.player_data.get(self.Requests.puuid, {}).get("team")
                
                msg_puuid = message['puuid']
                msg_player_data = self.player_data.get(msg_puuid, {})

                if msg_puuid == self.Requests.puuid:
                    clr = (221, 224, 41)
                elif msg_player_data.get("team") == self.ally_team:
                    clr = (76, 151, 237)
                else:
                    clr = (238, 77, 77)

                chat_indicator = message["cid"].split("@")[0].rsplit("-", 1)[1]
                chat_prefix = color("[Team]", fore=(116, 162, 214)) if chat_indicator == "blue" else "[All]"

                agent = self.colors.get_agent_from_uuid(msg_player_data.get('agent', '').lower())
                name = f"{message['game_name']}#{message['game_tag']}"
                
                if msg_player_data.get('streamer_mode') and self.hide_names and msg_puuid not in self.player_data.get("ignore", []):
                    self.print_message(f"{chat_prefix} {color(self.colors.escape_ansi(agent), clr)}: {message['body']}")
                    self.server.send_payload("chat", {
                        "time": message["time"], "puuid": msg_puuid, "self": msg_puuid == self.Requests.puuid,
                        "group": re.sub(r"\[|\]", "", self.colors.escape_ansi(chat_prefix)),
                        "agent": self.colors.escape_ansi(agent), "text": message['body']
                    })
                else:
                    agent_str = f" ({agent})" if agent else ""
                    self.print_message(f"{chat_prefix} {color(name, clr)}{agent_str}: {message['body']}")
                    self.server.send_payload("chat", {
                        "time": message["time"], "puuid": msg_puuid, "self": msg_puuid == self.Requests.puuid,
                        "group": re.sub(r"\[|\]", "", self.colors.escape_ansi(chat_prefix)),
                        "player": name, "agent": self.colors.escape_ansi(agent), "text": message['body']
                    })
                self.id_seen.append(message['id'])
        return None

    def print_message(self, message):
        self.messages += 1
        if self.messages > self.chat_limit:
            print(self.up * self.chat_limit, end="")
            for i in range(len(self.message_history) - self.chat_limit + 1, len(self.message_history)):
                print(self.message_history[i] + " " * max([0, len(self.colors.escape_ansi(self.message_history[i-1]).encode('utf8')) - len(self.colors.escape_ansi(self.message_history[i]).encode('utf8'))]))
            print(message + " " * max([0, len(self.colors.escape_ansi(self.message_history[-1]).encode('utf8')) - len(self.colors.escape_ansi(message).encode('utf8'))]))
        else:
            print(message)

        self.message_history.append(message)