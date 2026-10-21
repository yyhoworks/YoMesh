"""Tests for Hermes MCP integration (token rotation, installer download, and AI tools)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yomesh.server import handler_for


class HermesMcpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "web").mkdir(parents=True, exist_ok=True)
        (self.root / "scripts").mkdir(parents=True, exist_ok=True)
        (self.root / "examples").mkdir(parents=True, exist_ok=True)
        for script_name in ("yomesh_hermes_mcp.py", "hermes_installer.py"):
            (self.root / "scripts" / script_name).write_bytes((ROOT / "scripts" / script_name).read_bytes())
        (self.root / "examples" / "profile.json").write_bytes((ROOT / "examples" / "profile.json").read_bytes())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.handler = handler_for(self.root, self.server.server_port)

        class Quiet(self.handler):
            def log_message(self, *args):
                pass

        self.server.RequestHandlerClass = Quiet
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.handler.space_catalog.close()
        self.temporary.cleanup()

    def api(self, path: str, body: dict | None = None, headers: dict | None = None) -> tuple[int, dict]:
        req = Request(
            self.base + path,
            data=None if body is None else json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="GET" if body is None else "POST",
        )
        try:
            with urlopen(req, timeout=15) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except HTTPError as err:
            return err.code, json.loads(err.read().decode("utf-8"))

    def test_hermes_token_lifecycle_and_mcp_endpoints(self):
        # 1. Initial status is unconfigured
        status, info = self.api("/api/integrations/hermes")
        self.assertEqual(status, 200)
        self.assertFalse(info["configured"])

        # 2. Rotate generates a ymh_ token
        status, rotated = self.api("/api/integrations/hermes/rotate", {})
        self.assertEqual(status, 200)
        self.assertTrue(rotated["configured"])
        token = rotated["token"]
        self.assertTrue(token.startswith("ymh_"))

        # 3. Setup scripts are downloadable
        for script_name in ("yomesh_hermes_mcp.py", "hermes_installer.py"):
            with urlopen(f"{self.base}/api/setup/{script_name}", timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b"python3", resp.read())

        auth_headers = {"Authorization": f"Bearer {token}"}

        # 4. List spaces via Hermes API
        status, spaces_res = self.api("/api/hermes/spaces", headers=auth_headers)
        self.assertEqual(status, 200)
        self.assertEqual(spaces_res["active_space"], "default")
        self.assertEqual(len(spaces_res["spaces"]), 1)

        # 5. Add a rule via Hermes API and diagnose a matching domain
        status, add_res = self.api(
            "/api/hermes/rules/add",
            {"entry": "openai.com", "target": "DIRECT", "rule_name": "AI 直连测试"},
            headers=auth_headers,
        )
        self.assertEqual(status, 200, add_res)
        self.assertEqual(add_res["status"], "added")
        self.assertEqual(add_res["entry"], "DOMAIN-SUFFIX,openai.com")

        status, diag_res = self.api(
            "/api/hermes/diagnose",
            {"query": "api.openai.com"},
            headers=auth_headers,
        )
        self.assertEqual(status, 200, diag_res)
        self.assertEqual(diag_res["winner"]["matched_entry"], "DOMAIN-SUFFIX,openai.com")
        self.assertEqual(diag_res["winner"]["target"], "DIRECT")

        # 6. Revoke token blocks further Hermes requests
        status, revoked = self.api("/api/integrations/hermes/revoke", {})
        self.assertEqual(status, 200)
        self.assertFalse(revoked["configured"])

        status, denied = self.api("/api/hermes/spaces", headers=auth_headers)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
