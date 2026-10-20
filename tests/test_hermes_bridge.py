"""The stdio bridge advertises only read-and-prepare MCP tools."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "scripts" / "yomesh_hermes_mcp.py"
sys.path.insert(0, str(ROOT / "src"))

from yomesh import __version__
from yomesh.server import handler_for


class HermesBridgeTests(unittest.TestCase):
    def test_stdio_initialize_list_and_read_only_call(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            server.RequestHandlerClass = handler_for(root, server.server_port)
            handler = server.RequestHandlerClass
            handler.log_message = lambda *args: None
            handler.auth_store.update_credentials("", "admin", "fixture-password")
            token = handler.hermes_store.rotate()["token"]
            config = root / "bridge.json"
            config.write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_port}", "token": token}))
            os.chmod(config, 0o600)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            messages = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "yomesh_list_devices", "arguments": {}}},
            ]
            try:
                process = subprocess.run([sys.executable, str(BRIDGE), "--config", str(config)],
                                         input="".join(json.dumps(row) + "\n" for row in messages),
                                         text=True, capture_output=True, timeout=10, check=False)
                self.assertEqual(process.returncode, 0, process.stderr)
                rows = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertEqual([row["id"] for row in rows], [1, 2, 3])
                self.assertEqual(rows[0]["result"]["protocolVersion"], "2025-06-18")
                self.assertEqual(rows[0]["result"]["serverInfo"]["version"], __version__)
                names = {row["name"] for row in rows[1]["result"]["tools"]}
                self.assertIn("yomesh_prepare_release", names)
                self.assertFalse(any("approve" in name or "rollback" in name for name in names))
                self.assertEqual(json.loads(rows[2]["result"]["content"][0]["text"]), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                handler.space_catalog.close()
