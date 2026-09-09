import functools
import json
import logging
import os
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from websocket_server import WebsocketServer

from src.constants import version, PROJECT_ROOT

logging.getLogger('websocket_server.websocket_server').disabled = True

# websocket.enableTrace(True)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


class Server:
    def __init__(self, log, Error):
        self.Error = Error
        self.log = log
        self.lastMessages = {}
        self.docs_server = None
        self.docs_port = None

    def start_server(self):
        try:
            # print(self.lastMessage)
            with open(os.path.join(PROJECT_ROOT, "config.json"), "r") as conf:
                port = json.load(conf)["port"]
            self.server = WebsocketServer(host="0.0.0.0", port=port)
            # server = websocket.WebSocketApp("wss://localhost:1100", on_open=on_open, on_message=on_message, on_close=on_close)
            self.server.set_fn_new_client(self.handle_new_client)
            self.server.run_forever(threaded=True)
            self.start_docs_server(port)
        except Exception as e:
            self.Error.PortError(port)

    def start_docs_server(self, websocket_port):
        """Serve the bundled tracker UI over localhost so terminal links use HTTP."""
        docs_dir = os.path.join(PROJECT_ROOT, "docs")
        handler = functools.partial(_QuietStaticHandler, directory=docs_dir)

        preferred_port = websocket_port + 1 if int(websocket_port) < 65535 else 0
        try:
            self.docs_server = ThreadingHTTPServer(
                ("127.0.0.1", preferred_port), handler
            )
        except OSError:
            # Avoid failing vRY just because the adjacent port is already occupied.
            self.docs_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)

        self.docs_port = self.docs_server.server_address[1]
        threading.Thread(
            target=self.docs_server.serve_forever,
            name="vry-docs-server",
            daemon=True,
        ).start()
        self.log(f"tracker UI available at {self.get_match_loadouts_url(websocket_port)}")

    def get_match_loadouts_url(self, websocket_port):
        if self.docs_port is not None:
            return (
                f"http://127.0.0.1:{self.docs_port}/matchLoadouts.html"
                f"?port={int(websocket_port)}"
            )

        # Keep a browser-openable fallback if the local docs server was not started.
        return "https://vry.netlify.app/matchLoadouts"

    def handle_new_client(self, client, server):
        self.send_payload("version",{
            "core": version
        })
        for key in self.lastMessages:
            if key not in ["chat","version"]:
                self.send_message(self.lastMessages[key])

    def send_message(self, message):
        self.server.send_message_to_all(message)

    def send_payload(self, type, payload):
        payload["type"] = type
        msg_str = json.dumps(payload)
        self.lastMessages[type] = msg_str
        self.server.send_message_to_all(msg_str)
