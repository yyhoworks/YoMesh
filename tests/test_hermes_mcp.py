"""Tests for Hermes MCP integration (token rotation, installer download, and AI tools)."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yomesh.server import handler_for
from helpers import fetched_fixture


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
        # 1. Legacy v0.16.x format in data/hermes-access.json is migrated cleanly
        (self.root / "data").mkdir(parents=True, exist_ok=True)
        (self.root / "data/hermes-access.json").write_text(
            json.dumps({
                "format": "yomesh-hermes-v1",
                "token_sha256": "a" * 64,
                "created": 1700000000,
            }),
            encoding="utf-8",
        )
        status, info = self.api("/api/integrations/hermes")
        self.assertEqual(status, 200, info)
        self.assertTrue(info["configured"])

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

    def test_quick_setup_and_streamable_http_mcp(self):
        # 1. Presets endpoint returns region & rule presets
        status, presets = self.api("/api/spaces/presets")
        self.assertEqual(status, 200, presets)
        self.assertEqual(len(presets["regions"]), 5)
        self.assertEqual(len(presets["rules"]), 15)

        # 2. Quick region groups adds missing HK/TW/JP/SG/US groups in 1 click
        status, rg_res = self.api("/api/workspace/quick-region-groups", {})
        self.assertEqual(status, 200, rg_res)
        self.assertGreaterEqual(rg_res["added_count"], 1)
        # Second call is idempotent
        status, rg_res2 = self.api("/api/workspace/quick-region-groups", {})
        self.assertEqual(status, 200, rg_res2)
        self.assertEqual(rg_res2["added_count"], 0)

        # 3. Quick create space builds an isolated space even when default space has no g_default group
        _, default_ws = self.api("/api/workspace")
        default_prof = default_ws["profile"]
        default_prof["groups"] = [g for g in default_prof["groups"] if g["id"] != "g_default"]
        default_prof["fallback"] = "DIRECT"
        status, saved_def = self.api("/api/workspace", {"profile": default_prof, "revision": default_ws["revision"]})
        self.assertEqual(status, 200, saved_def)

        with patch("yomesh.quick_setup.fetch_subscription", side_effect=fetched_fixture):
            status, qc_res = self.api(
                "/api/spaces/quick-create",
                {
                    "name": "三步极速方案",
                    "proxy": "mihomo",
                    "subscription_url": "https://fixture.example/sub",
                    "regions": ["hk", "us", "sg"],
                    "rules": ["adblock", "ai", "youtube", "cn"],
                    "base_url": self.base,
                },
            )
        self.assertEqual(status, 200, qc_res)
        self.assertEqual(qc_res["summary"]["groups"], 4)  # Proxy + 3 regions
        self.assertEqual(qc_res["summary"]["policies"], 4)
        new_space_id = qc_res["id"]

        # 4. Streamable HTTP MCP (/mcp?token=...) supports JSON-RPC 2.0, batch rules, fuzzy group matching, and top priority
        _, rotated = self.api("/api/integrations/hermes/rotate", {})
        token = rotated["token"]
        status, init_rpc = self.api(
            f"/mcp?token={token}",
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        self.assertEqual(status, 200, init_rpc)
        self.assertEqual(init_rpc["result"]["serverInfo"]["name"], "yomesh-hermes-mcp")

        status, call_rpc = self.api(
            f"/mcp?token={token}",
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "yomesh_add_rule",
                    "arguments": {
                        "entry": "claude.ai, cursor.sh",
                        "target": "美国",
                        "space": new_space_id,
                    },
                },
            },
        )
        self.assertEqual(status, 200, call_rpc)
        payload = json.loads(call_rpc["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "added")
        self.assertEqual(payload["priority_rank"], 1)
        self.assertEqual(payload["target_name"], "US 美国节点")
        self.assertEqual(len(payload["entries"]), 2)

    def test_client_and_server_templates_import_and_exclude_personal_rules(self) -> None:
        js_path = Path(__file__).resolve().parents[1] / "web" / "space-editor.js"
        js_text = js_path.read_text(encoding="utf-8")

        # Verify Hermes block is removed and banner no longer duplicates the
        # template entry points (they live in the import dialog only).
        paint_section = js_text.split("function paintSpaceCards()", 1)[1].split("async function editSpace", 1)[0]
        self.assertNotIn("Hermes 智能助手 & HTTP MCP", paint_section)
        self.assertNotIn("space-template-client", paint_section)
        self.assertNotIn("space-template-server", paint_section)
        import_section = js_text.split("async function openConfigImport", 1)[1].split("const DEFAULT_PRESET_REGIONS", 1)[0]
        self.assertIn('data-ci-tpl="client"', import_section)
        self.assertIn('data-ci-tpl="server"', import_section)

        # Extract client and server template YAML from MIHOMO_TEMPLATES
        import re
        matches = dict(re.findall(r"(client|server):\{\s*name:'[^']+',\s*desc:'[^']+',\s*yaml:`([^`]+)`", js_text))
        self.assertEqual(set(matches.keys()), {"client", "server"})

        forbidden_markers = [
            "PT-Tracker",
            "PT-Domain",
            "Custom-Direct",
            "Custom-Proxy",
            "NoQUIC",
            "19302",
            "5349",
            "3478",
            "listeners:",
            "nameserver-policy:",
        ]
        for key, raw_yaml in matches.items():
            yaml_text = raw_yaml.replace("\\\\b", "\\b")
            for marker in forbidden_markers:
                self.assertNotIn(marker, yaml_text, f"{key} template still contains {marker}")

            _, cat = self.api("/api/spaces")
            status, imported = self.api(
                "/api/spaces/import-config",
                {
                    "name": f"模板测试-{key}",
                    "filename": f"{key}.yaml",
                    "text": yaml_text,
                    "mode": "auto",
                    "allow_draft": True,
                    "revision": cat["revision"],
                },
            )
            self.assertEqual(status, 200, imported)
            self.assertEqual(imported["import_summary"]["mode"], "native")
            space_entry = next(s for s in imported["spaces"] if s["id"] == imported["id"])
            self.assertTrue(space_entry["native"])


if __name__ == "__main__":
    unittest.main()


