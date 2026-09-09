import functools
import json
import logging
import os
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from websocket_server import WebsocketServer

from src.constants import version, PROJECT_ROOT

logging.getLogger('websocket_server.websocket_server').disabled = True

# websocket.enableTrace(True)


class _QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def _candidate_docs_dirs():
    """Return likely bundled docs directories for source and frozen builds."""
    roots = []

    # cx_Freeze places vry.exe beside the bundled docs/ directory.  Prefer that
    # location explicitly instead of relying on a module __file__ path, which can
    # point inside the frozen lib/ tree.
    if getattr(sys, "frozen", False):
        executable = getattr(sys, "executable", "")
        if executable:
            roots.append(Path(executable).resolve().parent)

    project_root = Path(PROJECT_ROOT).resolve()
    roots.append(project_root)

    # Be tolerant of frozen module paths that resolve PROJECT_ROOT to <app>/lib.
    if project_root.name.lower() == "lib":
        roots.append(project_root.parent)

    # Source launches may use the repository root as the current directory.
    roots.append(Path.cwd().resolve())

    seen = set()
    for root in roots:
        docs_dir = (root / "docs").resolve()
        key = os.path.normcase(str(docs_dir))
        if key in seen:
            continue
        seen.add(key)
        yield docs_dir


def _resolve_docs_dir():
    """Find a docs directory that actually contains the tracker entrypoint."""
    for docs_dir in _candidate_docs_dirs():
        if (docs_dir / "matchLoadouts.html").is_file():
            return docs_dir
    return None


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
        except Exception:
            self.Error.PortError(port)
            return

        # The tracker HTTP server is optional.  A problem locating/serving the
        # static UI must not make the working websocket server look like a port
        # or firewall failure.
        try:
            self.start_docs_server(port)
        except Exception as exc:
            self.docs_server = None
            self.docs_port = None
            fallback = self.get_match_loadouts_url(port)
            os.environ["VRY_TRACKER_URL"] = fallback
            self.log(f"tracker UI local server failed ({exc}); using {fallback}")

    def start_docs_server(self, websocket_port):
        """Serve the bundled tracker UI over localhost so terminal links use HTTP."""
        docs_dir = _resolve_docs_dir()
        if docs_dir is None:
            self.docs_server = None
            self.docs_port = None
            fallback = self.get_match_loadouts_url(websocket_port)
            os.environ["VRY_TRACKER_URL"] = fallback
            self.log(f"tracker UI files were not found; using {fallback}")
            return

        handler = functools.partial(_QuietStaticHandler, directory=str(docs_dir))

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

        browser_url = self.get_match_loadouts_url(websocket_port)
        os.environ["VRY_TRACKER_URL"] = browser_url
        self.log(f"tracker UI serving {docs_dir} at {browser_url}")

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
