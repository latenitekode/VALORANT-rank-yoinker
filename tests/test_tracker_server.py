import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import urlopen

import src.server as server_module


class TrackerServerTests(unittest.TestCase):
    def test_frozen_build_prefers_docs_beside_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            (docs / "matchLoadouts.html").write_text("tracker-ok", encoding="utf-8")
            (root / "lib").mkdir()

            with patch.object(server_module, "PROJECT_ROOT", root / "lib"), patch.object(
                server_module.sys, "frozen", True, create=True
            ), patch.object(server_module.sys, "executable", str(root / "vry.exe")):
                self.assertEqual(server_module._resolve_docs_dir(), docs.resolve())

    def test_local_tracker_server_serves_entrypoint_from_frozen_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docs = root / "docs"
            docs.mkdir()
            (docs / "matchLoadouts.html").write_text("tracker-ok", encoding="utf-8")
            (root / "lib").mkdir()

            logs = []
            tracker = server_module.Server(logs.append, Error=None)

            with patch.object(server_module, "PROJECT_ROOT", root / "lib"), patch.object(
                server_module.sys, "frozen", True, create=True
            ), patch.object(server_module.sys, "executable", str(root / "vry.exe")):
                tracker.start_docs_server(65535)
                try:
                    url = tracker.get_match_loadouts_url(65535)
                    with urlopen(url, timeout=2) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.read().decode("utf-8"), "tracker-ok")
                finally:
                    tracker.docs_server.shutdown()
                    tracker.docs_server.server_close()


if __name__ == "__main__":
    unittest.main()
