"""Exercise the real persistent HTTP workflow without external subscriptions."""
import json
import base64
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.server import handler_for
from yomesh.workspace import Workspace

URI = "ss://aes-128-gcm:fixture@127.0.0.1:22001#HK-test"


class WorkspaceAPITests(unittest.TestCase):
    def test_base_draft_preview_uses_backend_profile_without_persistence(self):
        before = self.state()
        # Explicit blank selection avoids the initial empty proxy group draft.
        profile = {"schema_version": 1, "sources": [], "nodes": [], "groups": [], "library": [], "policies": [], "fallback": "DIRECT", "settings": {}}
        code, saved = self.request("/api/workspace", {"profile": profile, "revision": before["revision"]})
        self.assertEqual(code, 200)
        body = {"target": "sing-box", "text": '{"log":{"level":"warn"}}', "revision": saved["revision"]}
        code, preview = self.request("/api/workspace/preview-base-config", body)
        self.assertEqual(code, 200, preview)
        self.assertEqual(json.loads(preview["files"]["sing-box.json"])["log"]["level"], "warn")
        self.assertNotIn("mosdns.yaml", preview["files"])
        self.assertEqual(self.state(), saved)
        self.assertEqual(self.request("/api/workspace/preview-base-config", {**body, "revision": saved["revision"] - 1})[0], 409)

    def test_selection_preview_http_is_read_only_and_rejects_stale_revisions(self):
        before = self.state()
        code, preview = self.request("/api/workspace/preview-selection", {"selection": {"groups": [], "rules": [], "policies": [], "fallback": "DIRECT"}, "revision": before["revision"]})
        self.assertEqual(code, 200, preview)
        self.assertTrue(preview["valid"])
        self.assertEqual(self.state(), before)
        self.assertEqual(self.request("/api/workspace/preview-selection", {"selection": {}, "revision": before["revision"]-1})[0], 409)
        self.assertEqual(self.request("/api/workspace/preview-selection", {"selection": {"rules": ["missing"]}, "revision": before["revision"]})[0], 422)

    def test_http_space_headers_keep_writes_and_reads_isolated(self):
        code, catalog = self.request("/api/spaces")
        self.assertEqual(code, 200)
        code, created = self.request("/api/spaces/save", {"revision": catalog["revision"], "copy_from": "default", "space": {"name": "手机", "proxy": "sing-box"}})
        self.assertEqual(code, 200, created)
        phone = {"X-YoMesh-Space": created["id"]}
        phone_revision = self.request("/api/workspace", headers=phone)[1]["revision"]
        code, saved = self.request("/api/workspace/base-config", {"target": "mihomo", "text": "log-level: warning", "revision": phone_revision}, headers=phone)
        self.assertEqual(code, 200, saved)
        self.assertNotIn("base_configs", self.state()["profile"])
        self.assertEqual(self.request("/api/workspace/base-configs", headers=phone)[1]["stored"]["mihomo"], "log-level: warning")
        code, selected = self.request("/api/spaces/select", {"id": created["id"], "revision": self.request("/api/spaces")[1]["revision"]})
        self.assertEqual(code, 200, selected)
        self.assertIn("base_configs", self.state()["profile"])
        self.assertNotIn("base_configs", self.request("/api/workspace", headers={"X-YoMesh-Space": "default"})[1]["profile"])
        self.assertEqual(self.request("/api/workspace", headers={"X-YoMesh-Space": "missing"})[0], 422)

    def test_rule_can_change_between_subscription_and_matcher_with_same_reference(self):
        self.payload = "payload:\n  - DOMAIN,example.com\n"
        item = {"id": "stable_rule", "name": "可切换规则", "source": {"type": "url", "format": "clash", "url": f"http://127.0.0.1:{self.upstream.server_port}/rules", "auto_update": False}}
        code, saved = self.request("/api/library/save-batch", {"items": [item], "revision": self.state()["revision"]})
        self.assertEqual(code, 200, saved)
        item = {"id": "stable_rule", "name": "可切换规则", "source": {"type": "manual"}, "matchers": [{"type": "DOMAIN-SUFFIX", "values": ["example.net"]}]}
        code, saved = self.request("/api/library/save-batch", {"items": [item], "revision": saved["revision"]})
        self.assertEqual(code, 200, saved)
        self.assertEqual(len(saved["profile"]["library"]), 1)
        self.assertEqual(saved["profile"]["library"][0]["id"], "stable_rule")
        self.assertEqual(saved["profile"]["library"][0]["source"]["type"], "manual")

    def test_batch_rule_subscriptions_are_atomic_and_matchers_use_shared_model(self):
        before = self.state()
        base = f"http://127.0.0.1:{self.upstream.server_port}"
        self.payload = 'payload:\n  - DOMAIN,example.com\n'
        items = [{"name": "订阅一", "source": {"type": "url", "format": "clash", "url": base + "/one", "auto_update": False}}, {"name": "订阅二", "source": {"type": "url", "format": "sing-box", "url": base + "/two", "auto_update": False}}]
        code, result = self.request("/api/library/save-batch", {"items": items, "revision": before["revision"]})
        self.assertEqual(code, 422)
        self.assertEqual(self.state(), before)
        items[1]["source"]["format"] = "clash"
        code, saved = self.request("/api/library/save-batch", {"items": items, "revision": before["revision"]})
        self.assertEqual(code, 200, saved)
        self.assertEqual(len(saved["profile"]["library"]), 2)
        self.assertEqual(saved["revision"], before["revision"] + 1)
        matcher = {"name": "本地匹配器", "source": {"type": "manual"}, "matchers": [{"type": "DOMAIN-SUFFIX", "values": ["example.net"]}, {"type": "IP-CIDR6", "values": ["fc00::/7"]}]}
        code, saved = self.request("/api/library/save-batch", {"items": [matcher], "revision": saved["revision"]})
        self.assertEqual(code, 200, saved)
        item = saved["profile"]["library"][-1]
        code, editor = self.request("/api/library/prepare-editor", {"id": item["id"]})
        self.assertEqual(code, 200)
        self.assertEqual(editor["criteria"], [["DOMAIN-SUFFIX", "example.net"], ["IP-CIDR6", "fc00::/7"]])

    def test_new_workspace_can_add_inbound_after_creating_default_plan(self):
        state = self.state()
        code, state = self.request("/api/workspace/ensure-plan", {"revision": state["revision"]})
        self.assertEqual(code, 200, state)
        self.assertEqual(state["profile"]["active_plan"], "default")
        value = state["profile"]
        value["inbounds"] = [{"id": "new_home", "name": "新增入站", "protocol": "mixed", "listen": "127.0.0.1", "port": 22101, "plan": "default", "enabled": True}]
        code, saved = self.request("/api/workspace", {"profile": value, "revision": state["revision"]})
        self.assertEqual(code, 200, saved)
        self.assertEqual(saved["profile"]["inbounds"][0]["plan"], "default")

    def test_base_config_editor_save_and_reset_over_http(self):
        state = self.state()
        code, editor = self.request("/api/workspace/base-configs")
        self.assertEqual(code, 200)
        self.assertIn("mixed-port", editor["defaults"]["mihomo"])
        code, state = self.request("/api/workspace/base-config", {"target": "mihomo", "text": "# custom\nlog-level: warning\n", "revision": state["revision"]})
        self.assertEqual(code, 200, state)
        self.assertIn("# custom", state["profile"]["base_configs"]["mihomo"])
        code, state = self.request("/api/workspace/base-config", {"target": "mihomo", "text": "", "revision": state["revision"]})
        self.assertEqual(code, 200, state)
        self.assertEqual(state["profile"]["base_configs"]["mihomo"], "")

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        (root / "web").mkdir()
        self.payload = URI
        outer = self

        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(outer.payload.encode())

            def log_message(self, *args):
                pass

        self.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        handler = handler_for(root, self.server.server_port)

        class QuietHandler(handler):
            def log_message(self, *args):
                pass

        self.server.RequestHandlerClass = QuietHandler
        for server in (self.upstream, self.server):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.RequestHandlerClass.space_catalog.close()
        for server in (self.upstream, self.server):
            server.shutdown()
            server.server_close()
        self.temporary.cleanup()

    def request(self, path, value=None, headers=None):
        request = Request(f"http://127.0.0.1:{self.server.server_port}" + path,
                          data=None if value is None else json.dumps(value).encode(),
                          headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=4) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def state(self):
        code, state = self.request("/api/workspace")
        self.assertEqual(code, 200)
        return state

    def test_library_import_compound_preview_save_and_reference_guard(self):
        state = self.state()
        code, state = self.request("/api/library/save", {"item": {"name": "测试规则集", "source": {"type": "manual", "format": "auto"}, "text": "+.example.com\n192.168.0.0/16"}, "revision": state["revision"]})
        self.assertEqual(code, 200)
        ident = state["profile"]["library"][0]["id"]
        policy = {"id": "compound", "name": "测试条件组", "type": "LOGICAL", "condition_groups": [{"operator": "and", "conditions": [{"type": "RULE-SET", "value": ident}, {"type": "DOMAIN-KEYWORD", "values": ["www"]}]}], "group_operator": "or", "target": "DIRECT", "enabled": True}
        code, preview = self.request("/api/workspace/preview-rule", {"profile": state["profile"], "policy": policy})
        self.assertEqual(code, 200)
        self.assertIn("且", preview["summary"])
        self.assertTrue(preview["has_ip"])
        value = {**state["profile"], "groups": [], "policies": [policy], "fallback": "DIRECT", "routing_plans": [], "inbounds": []}
        code, result = self.request("/api/workspace/preview-export", {"proxy": "sing-box", "profile": value, "rule_output": "local"})
        self.assertEqual(code, 200, result)
        self.assertEqual(result["manifest"]["validation"]["status"], "not-run")
        self.assertEqual(result["manifest"]["rule_output"]["mode"], "local")
        self.assertIn("sing-box.json", result["files"])
        self.assertTrue(any(name.startswith("rules/") for name in result["files"]))
        self.assertEqual(self.state()["revision"], state["revision"])
        state["profile"].update(policies=[policy], fallback="DIRECT")
        code, state = self.request("/api/workspace", {"profile": state["profile"], "revision": state["revision"]})
        self.assertEqual(code, 200)
        code, error = self.request(f"/api/library/{ident}/delete", {"revision": state["revision"]})
        self.assertEqual(code, 422)
        self.assertIn("引用", error["error"])

    def test_uploaded_rule_file_is_parsed_through_http_and_persisted(self):
        content = b'payload:\n  - DOMAIN-SUFFIX,example.com\n  - IP-CIDR6,fc00::/7\n'
        code, state = self.request("/api/library/save", {"item": {"name": "上传规则", "source": {"type": "file", "format": "auto", "filename": "rules.yaml"}, "file_base64": base64.b64encode(content).decode()}, "revision": self.state()["revision"]})
        self.assertEqual(code, 200)
        item = state["profile"]["library"][0]
        self.assertEqual(item["entries"], ["DOMAIN-SUFFIX,example.com", "IP-CIDR6,fc00::/7"])
        self.assertEqual(item["source"]["filename"], "rules.yaml")

    def add_source(self, name="fixture", ident=None):
        source = {"name": name, "url": f"http://127.0.0.1:{self.upstream.server_port}/sub", "auto_update": False}
        if ident:
            source["id"] = ident
        return self.request("/api/sources", {"source": source, "revision": self.state()["revision"]})

    def refresh(self, ident):
        self.assertEqual(self.request(f"/api/sources/{ident}/refresh", {})[0], 200)
        service = self.server.RequestHandlerClass.subscription_service
        deadline = time.monotonic() + 3
        while service.busy(ident) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(service.busy(ident))
        return self.state()

    def test_add_refresh_persist_and_delete(self):
        code, result = self.add_source()
        self.assertEqual(code, 200)
        ident = result["id"]
        state = self.refresh(ident)
        self.assertEqual(len(state["profile"]["nodes"]), 1)
        restarted = Workspace(Path(self.temporary.name) / "data").snapshot()
        self.assertEqual(restarted["profile"], state["profile"])
        code, state = self.request(f"/api/sources/{ident}/delete", {"revision": state["revision"]})
        self.assertEqual(code, 200)
        self.assertEqual(state["profile"]["nodes"], [])
        self.assertEqual(state["profile"]["groups"][0]["sources"], [])

    def test_refresh_failure_keeps_existing_nodes(self):
        _, result = self.add_source()
        before = self.refresh(result["id"])
        self.payload = "<html>upstream error</html>"
        after = self.refresh(result["id"])
        self.assertEqual(after["profile"]["nodes"], before["profile"]["nodes"])
        self.assertEqual(after["source_status"][result["id"]]["refresh_state"], "error")

    def test_bad_regex_and_duplicate_id_do_not_mutate(self):
        _, result = self.add_source(ident="fixture")
        state = self.state()
        source = {**state["profile"]["sources"][0], "include": "["}
        code, _ = self.request("/api/sources/fixture/save", {"source": source, "revision": state["revision"]})
        self.assertEqual(code, 422)
        self.assertEqual(self.state(), state)
        self.assertEqual(self.add_source("other", "fixture")[0], 422)

    def test_disabled_subscription_cannot_refresh(self):
        _, result = self.add_source()
        state = self.state()
        source = {**state["profile"]["sources"][0], "enabled": False}
        self.assertEqual(self.request(f"/api/sources/{result['id']}/save", {"source": source, "revision": state["revision"]})[0], 200)
        self.assertEqual(self.request(f"/api/sources/{result['id']}/refresh", {})[0], 422)

    def test_server_preview_and_rule_persistence(self):
        _, result = self.add_source()
        state = self.refresh(result["id"])
        code, value = self.request("/api/workspace/preview-group", {"profile": state["profile"], "group_id": "g_default"})
        self.assertEqual((code, len(value["members"])), (200, 1))
        profile = state["profile"]
        profile["library"].append({"id": "l_test", "name": "test", "kind": "domain", "entries": ["+.example.com"]})
        profile["policies"].append({"id": "p_test", "name": "test", "type": "RULE-SET", "value": "l_test", "target": "DIRECT", "enabled": True})
        self.assertEqual(self.request("/api/workspace", {"profile": profile, "revision": state["revision"]})[0], 200)
        self.assertEqual(self.state()["profile"]["policies"][0]["target"], "DIRECT")
        self.assertEqual(self.request("/api/workspace", {"profile": profile, "revision": state["revision"]})[0], 409)

    def test_bad_input_and_reverse_proxy_hosts_are_accepted(self):
        self.assertEqual(self.request("/api/sources", {"source": [], "revision": 0})[0], 422)
        # Host/Origin checking was removed so reverse proxies work unchanged.
        self.assertEqual(self.request("/api/workspace", headers={"Origin": "https://proxy.example"})[0], 200)
        self.assertEqual(self.request("/api/workspace", headers={"Host": "proxy.example:9443"})[0], 200)

    def test_wildcard_allowed_host_accepts_reverse_proxy_hosts(self):
        # Retained coverage: every host is accepted directly; no extra server
        # flags are needed anymore.
        self.assertEqual(self.request("/api/health", headers={"Host": "proxy.example.com:9443"})[0], 200)

    def test_dns_settings_preview_and_policy_reorder_http(self):
        state = self.state()
        settings = {**state["usage"]["settings"], "dns_default": "local"}
        code, preview = self.request("/api/workspace/preview-dns", {"settings": settings})
        self.assertEqual(code, 200)
        self.assertIn("config", preview)
        self.assertEqual(self.state()["revision"], state["revision"])
        code, saved = self.request("/api/workspace/settings", {"settings": settings, "revision": state["revision"]})
        self.assertEqual(code, 200)
        self.assertEqual(saved["profile"]["settings"], settings)
        self.assertEqual(self.request("/api/workspace/settings", {"settings": settings, "revision": state["revision"]})[0], 409)
        self.assertEqual(self.request("/api/workspace/reorder-policies", {"ids": [], "plan": "wrong", "revision": saved["revision"]})[0], 409)
        self.assertEqual(self.request("/api/nodes/test", {"ids": ["missing"]})[0], 422)


if __name__ == "__main__":
    unittest.main()
