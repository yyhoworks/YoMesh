"""Unified editing preserves original native routing until the user changes it."""
import json
import io
import sys
import tempfile
import threading
import unittest
import zipfile
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle, find_core
from yomesh.native_configuration import checked, summary
from yomesh.native_policy import change
from yomesh.native_rule_groups import overview, rule_groups, preview, prepare_group
from yomesh.server import handler_for


def grouped_fixture():
    n = fixture()
    n["mihomo"]["proxy-groups"] = [
        {"name": "HK", "type": "select", "proxies": ["DIRECT"]},
        {"name": "JP", "type": "select", "proxies": ["DIRECT"]},
        {"name": "Common", "type": "select", "proxies": ["HK", "JP"]},
        {"name": "AI", "type": "select", "proxies": ["Common", "HK", "JP"], "lazy": True},
        {"name": "Media", "type": "select", "proxies": ["Common", "HK", "JP"]},
    ]
    n["group_roles"] = {"HK": "nodes", "JP": "nodes"}
    n["mihomo"]["rule-providers"].update({"domain": {"type": "inline", "behavior": "domain", "payload": ["video.test"]}, "ip": {"type": "inline", "behavior": "ipcidr", "payload": ["192.0.2.0/24"]}})
    n["policies"] = [
        {"id": "ai", "text": "RULE-SET,one,AI", "enabled": True},
        {"id": "media", "text": "RULE-SET,domain,Media", "enabled": True},
        {"id": "media_ip", "text": "RULE-SET,ip,Media,no-resolve", "enabled": True},
        {"id": "final", "text": "MATCH,Common", "enabled": True},
    ]
    return n


def edit_request(n, ident, **changes):
    unit = next(u for u in rule_groups(n) if u["id"] == ident)
    request = {"action": "unit-save", "id": ident, "draft": {**deepcopy(unit["editor"]), "name": unit["name"], "enabled": unit["enabled"]}, "outlet": deepcopy(unit["outlet"])}
    request.update(changes)
    return request


class UnifiedRuleGroupTests(unittest.TestCase):
    def test_member_picker_exposes_manual_nodes_and_groups_without_credentials(self):
        n = grouped_fixture()
        n["mihomo"]["proxies"] = [{"name": "manual", "type": "ss", "server": "example.test", "port": 443, "password": "private-value", "cipher": "aes-128-gcm"}]
        choices = {item["value"]: item for item in overview(n)["group_member_choices"]}
        self.assertEqual(choices["manual"]["resource"], "node")
        self.assertEqual(choices["HK"]["resource"], "group")
        self.assertEqual(choices["DIRECT"]["resource"], "builtin")
        self.assertIn("HK", choices["AI"]["blocked_for"])
        self.assertNotIn("JP", choices["HK"]["blocked_for"])
        self.assertNotIn("private-value", json.dumps(choices))

    def test_group_locations_lead_to_the_owned_rule_or_shared_editor(self):
        n = grouped_fixture()
        before = deepcopy(n)
        view = overview(n)
        locations = {item["name"]: item for item in view["group_management"]}
        self.assertEqual(locations["AI"]["editor"], {"type": "rule", "id": "ai"})
        self.assertEqual(locations["Media"]["owners"], [{"id": "media", "name": "Media"}])
        self.assertEqual(locations["Common"]["editor"], {"type": "group", "name": "Common", "role": "shared"})
        self.assertEqual(locations["HK"]["role"], "nodes")
        self.assertEqual(locations["HK"]["section"], "manual")
        self.assertEqual(locations["AI"]["section"], "rules")
        self.assertEqual(locations["Common"]["section"], "shared")
        choices = {item["value"]: item for item in view["group_member_choices"]}
        self.assertEqual(choices["AI"]["label"], "规则组出口 · AI")
        self.assertEqual(choices["AI"]["editor"], locations["AI"]["editor"])
        self.assertNotIn("AI", [item["value"] for item in view["outlet_choices"]])
        self.assertEqual(n, before)

    def test_unbound_outlet_still_has_an_editor_and_keeps_original_members(self):
        n = grouped_fixture()
        n["mihomo"]["proxy-groups"].append({"name": "Unused", "type": "select", "proxies": ["HK", "JP"], "unknown-field": "preserve"})
        view = overview(n)
        location = next(m for m in view["group_management"] if m["name"] == "Unused")
        self.assertEqual(location["owners"], [])
        self.assertEqual(location["editor"], {"type": "group", "name": "Unused", "role": "rules"})
        self.assertIn("Unused", view["unbound_outlets"])
        self.assertEqual(location["category"], "shared")
        self.assertIn("Unused", view["node_groups"])

    def test_navigation_categories_exclude_owned_rule_outlets_and_trace_usage(self):
        n = grouped_fixture()
        n["mihomo"]["proxy-groups"][0]["use"] = ["source"]
        before = deepcopy(n)
        locations = {m["name"]: m for m in overview(n)["group_management"]}
        self.assertEqual(locations["HK"]["category"], "lines")
        self.assertEqual(locations["JP"]["category"], "home")
        self.assertEqual(locations["Common"]["category"], "shared")
        self.assertIsNone(locations["AI"]["category"])
        self.assertIsNone(locations["Media"]["category"])
        self.assertEqual({u["id"] for u in locations["HK"]["used_by_rules"]}, {"ai", "media", "final"})
        self.assertEqual(n, before)

    def test_direct_source_fields_preserve_unedited_sources_without_fetch(self):
        n = grouped_fixture()
        n["mihomo"]["rule-providers"]["one"] = {"type": "http", "url": "https://example.test/one.yaml", "path": "ruleset/one.yaml", "behavior": "domain", "header": {"User-Agent": ["original"]}, "interval": 3600}
        n["documents"]["ruleset/one.yaml"] = "payload:\n  - example.com\n"
        unit = rule_groups(n)[0]
        self.assertEqual(unit["source_settings"][0]["url"], "https://example.test/one.yaml")
        self.assertEqual(rule_groups(n)[1]["source_settings"][0]["body"], "payload:\n- video.test\n")
        request = edit_request(n, "ai", source_edits=[{"ref": "one", "url": "https://example.test/one.yaml"}])
        with patch("yomesh.native_configuration.refresh_provider") as fetch:
            self.assertEqual(change(n, request), n)
            fetch.assert_not_called()

    def test_direct_subscription_edit_is_atomic_and_preview_does_not_fetch(self):
        n = grouped_fixture()
        n["mihomo"]["rule-providers"]["one"] = {"type": "http", "url": "https://example.test/one.yaml", "path": "ruleset/one.yaml", "behavior": "domain", "interval": 3600}
        n["documents"]["ruleset/one.yaml"] = "payload:\n  - example.com\n"
        before = deepcopy(n)
        request = edit_request(n, "ai", source_edits=[{"ref": "one", "url": "https://example.test/new.yaml"}], outlet={"type": "select", "members": ["JP"], "default": "JP"})
        with patch("yomesh.native_configuration.refresh_provider") as fetch:
            preview(n, request)
            fetch.assert_not_called()
        with patch("yomesh.native_configuration.refresh_provider", side_effect=ConfigError("source failed")):
            with self.assertRaises(ConfigError):
                change(n, request)
        self.assertEqual(n, before)
        def downloaded(value, section, ref):
            value = deepcopy(value)
            value["documents"][value["mihomo"][section][ref]["path"]] = "payload:\n  - new.test\n"
            return value
        with patch("yomesh.native_configuration.refresh_provider", side_effect=downloaded):
            result = change(n, request)
        self.assertEqual(result["mihomo"]["rule-providers"]["one"]["url"], "https://example.test/new.yaml")
        self.assertEqual(result["mihomo"]["rule-providers"]["one"]["interval"], 3600)
        self.assertEqual(result["policies"], n["policies"])

    def test_direct_shared_source_edit_creates_copy_and_keeps_native_parts(self):
        n = grouped_fixture()
        n["policies"].insert(0, {"id": "another", "text": "RULE-SET,ip,DIRECT,no-resolve", "enabled": True})
        request = edit_request(n, "media", source_edits=[{"ref": "ip", "body": "payload:\n- 198.51.100.0/24\n"}])
        result = change(n, request)
        self.assertEqual(result["mihomo"]["rule-providers"]["ip"], n["mihomo"]["rule-providers"]["ip"])
        rows = next(u for u in rule_groups(result) if u["id"] == "media")
        copied = rows["editor"]["rules"][1]
        self.assertNotEqual(copied, "ip")
        self.assertEqual(result["policies"][3]["text"], f"RULE-SET,{copied},Media,no-resolve")
        self.assertEqual(result["policies"][0], n["policies"][0])
        self.assertEqual(result["mihomo"]["rule-providers"][copied]["payload"], ["198.51.100.0/24"])

    def test_direct_source_edit_rejects_unattached_duplicate_or_invalid_fields(self):
        n = grouped_fixture()
        for edits in ([{"ref": "domain", "body": "payload: []"}], [{"ref": "one", "body": "invalid"}], [{"ref": "one", "body": "payload: []"}] * 2):
            with self.assertRaises(ConfigError):
                change(n, edit_request(n, "ai", source_edits=edits))

    def test_counts_use_editor_groups_and_retain_native_counts_separately(self):
        result = summary(grouped_fixture())
        self.assertEqual((result["counts"]["groups"], result["counts"]["library"], result["counts"]["policies"]), (3, 2, 3))
        self.assertEqual((result["native_counts"]["groups"], result["native_counts"]["policies"]), (5, 4))

    def test_http_atomic_edit_revision_and_download_use_the_same_native_parts(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            handler = handler_for(Path(directory), server.server_port)
            class Quiet(handler):
                def log_message(self, *args):
                    pass
            server.RequestHandlerClass = Quiet
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(path, body=None, scope=None, raw=False):
                headers = {"Content-Type": "application/json", **({"X-YoMesh-Space": scope} if scope else {})}
                try:
                    with urlopen(Request(f"http://127.0.0.1:{server.server_port}" + path, data=json.dumps(body).encode() if body is not None else None, headers=headers), timeout=30) as response:
                        return response.status, response.read() if raw else json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                n = grouped_fixture()
                code, catalog = request("/api/spaces/import-native", {"draft": True, "name": "group test", "configuration": n, "revision": handler.space_catalog.state["revision"]})
                self.assertEqual(code, 200)
                ident = catalog["id"]
                _, before = request("/api/workspace", scope=ident)
                edit = edit_request(n, "media", outlet={"type": "select", "members": ["HK", "JP"], "default": "JP"})
                edit["revision"] = before["revision"]
                self.assertEqual(request("/api/workspace/native-policy-preview", {**edit, "unit": True}, ident)[0], 200)
                self.assertEqual(request("/api/workspace", scope=ident)[1], before)
                code, saved = request("/api/workspace/native-policy", edit, ident)
                self.assertEqual(code, 200, saved)
                self.assertEqual(saved["native_view"]["rule_groups"][1]["outlet"]["default"], "JP")
                self.assertEqual(saved["profile"]["native_configuration"]["policies"], n["policies"])
                self.assertEqual(request("/api/workspace/native-policy", edit, ident)[0], 409)
                with patch("yomesh.native.find_core", side_effect=lambda root, name: find_core(ROOT, name)):
                    code, payload = request("/api/export", {"use_workspace": True, "component": "proxy", "proxy": "mihomo", "rule_output": "remote"}, ident, raw=True)
                self.assertEqual(code, 200, payload if code != 200 else "")
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual(manifest["validation"]["status"], "passed")
                    self.assertIn(b'RULE-SET,ip,Media,no-resolve', archive.read("mihomo.yaml"))
            finally:
                handler.space_catalog.close()
                server.shutdown()
                server.server_close()

    def test_view_joins_adjacent_domain_and_ip_without_changing_export(self):
        n = grouped_fixture()
        original = deepcopy(n)
        before = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": n}, rule_output="remote", component="proxy")
        view = overview(n)
        self.assertEqual(view["node_groups"], ["HK", "JP", "Common"])
        self.assertEqual(view["shared_outlets"], ["Common"])
        self.assertEqual([u["name"] for u in view["rule_groups"]], ["AI", "Media", "兜底策略"])
        self.assertEqual(view["rule_groups"][1]["policy_ids"], ["media", "media_ip"])
        self.assertEqual(view["rule_groups"][1]["editor"]["rules"], ["domain", "ip"])
        self.assertNotIn("AI", {c["value"] for c in view["outlet_choices"]})
        self.assertEqual(n, original)
        self.assertEqual(compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": n}, rule_output="remote", component="proxy"), before)

    def test_open_and_save_keeps_parameters_and_unknown_outlet_fields(self):
        n = grouped_fixture()
        n["mihomo"]["proxy-groups"][-1]["future-option"] = {"retained": True}
        for ident in ("ai", "media", "final"):
            result = change(n, edit_request(n, ident))
            self.assertEqual(result, n)
        result = change(n, edit_request(n, "media", outlet={"type": "select", "members": ["Common", "HK", "JP"], "default": "JP"}))
        outlet = next(g for g in result["mihomo"]["proxy-groups"] if g["name"] == "Media")
        self.assertEqual(outlet["proxies"], ["JP", "Common", "HK"])
        self.assertEqual(outlet["future-option"], {"retained": True})
        self.assertEqual(result["policies"], n["policies"])

    def test_one_save_updates_sources_and_outlet_together(self):
        n = grouped_fixture()
        request = edit_request(n, "ai", outlet={"type": "url-test", "members": ["HK", "JP"], "interval": 600, "tolerance": 80, "test_url": "https://example.com/check"})
        request["draft"].update(rules=["one", "domain"])
        result = change(n, request)
        self.assertEqual(result["policies"][0]["text"], "OR,((RULE-SET,one),(RULE-SET,domain)),AI")
        group = next(g for g in result["mihomo"]["proxy-groups"] if g["name"] == "AI")
        self.assertEqual((group["type"], group["proxies"], group["interval"], group["lazy"]), ("url-test", ["HK", "JP"], 600, True))
        bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": result}, rule_output="remote", component="proxy")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_group_sort_and_toggle_keep_parts_and_final_position(self):
        n = grouped_fixture()
        result = change(n, {"action": "unit-reorder", "ids": ["media", "ai", "final"]})
        self.assertEqual([p["id"] for p in result["policies"]], ["media", "media_ip", "ai", "final"])
        self.assertEqual(result["policies"][1]["text"], "RULE-SET,ip,Media,no-resolve")
        result = change(result, {"action": "unit-toggle", "id": "media", "enabled": False})
        self.assertEqual([p["enabled"] for p in result["policies"]], [False, False, True, True])
        for request in ({"action": "unit-reorder", "ids": ["ai", "final", "media"]}, {"action": "unit-toggle", "id": "final", "enabled": False}, {"action": "unit-delete", "id": "final"}):
            with self.assertRaises(ConfigError):
                change(n, request)

    def test_partial_enabled_parts_and_removing_one_source_preserve_options(self):
        n = grouped_fixture()
        n["policies"][2]["enabled"] = False
        self.assertTrue(rule_groups(n)[1]["mixed_enabled"])
        self.assertEqual(change(n, edit_request(n, "media")), n)
        request = edit_request(n, "media")
        request["draft"]["rules"] = ["ip"]
        result = change(n, request)
        self.assertEqual(result["policies"][1], {"id": "media", "text": "RULE-SET,ip,Media,no-resolve", "enabled": False})

    def test_copy_has_independent_application_outlet_and_delete_keeps_sources(self):
        n = grouped_fixture()
        copied = change(n, {"action": "unit-copy", "id": "media"})
        units = rule_groups(copied)
        clone = units[2]
        self.assertEqual(len(clone["policy_ids"]), 2)
        self.assertNotEqual(clone["target"], "Media")
        request = edit_request(copied, clone["id"], outlet={"type": "select", "members": ["JP"], "default": "JP"})
        edited = change(copied, request)
        original = next(g for g in edited["mihomo"]["proxy-groups"] if g["name"] == "Media")
        self.assertEqual(original["proxies"], ["Common", "HK", "JP"])
        removed = change(edited, {"action": "unit-delete", "id": clone["id"]})
        self.assertEqual(removed["policies"], n["policies"])
        self.assertEqual(removed["mihomo"]["rule-providers"], n["mihomo"]["rule-providers"])
        self.assertIn(clone["target"], overview(removed)["unbound_outlets"])

    def test_new_group_is_visible_only_with_rule_and_validates_cycle_members(self):
        n = grouped_fixture()
        n["selection"] = {"groups": ["HK", "JP", "Common", "AI", "Media"]}
        request = {"action": "unit-save", "draft": {"name": "New app", "mode": "rules", "rules": ["one"], "target": "DIRECT"}, "outlet": {"type": "select", "members": ["HK", "JP"], "default": "JP"}}
        result = change(n, request)
        group = rule_groups(result)[-2]
        self.assertEqual(group["name"], "New app")
        self.assertIn(group["target"], result["selection"]["groups"])
        self.assertNotIn(group["target"], overview(result)["node_groups"])
        for members in (["absent"], ["AI"], ["HK", "HK"], []):
            with self.assertRaises(ConfigError):
                change(n, edit_request(n, "ai", outlet={"type": "select", "members": members}))
        n["mihomo"]["proxy-groups"][1]["proxies"] = ["AI"]
        with self.assertRaisesRegex(ConfigError, "循环"):
            change(n, edit_request(n, "ai", outlet={"type": "select", "members": ["JP"]}))

    def test_nonadjacent_or_advanced_policies_do_not_reorder_or_collapse(self):
        n = grouped_fixture()
        n["policies"][1:3] = [n["policies"][1], {"id": "middle", "text": "DOMAIN,other.test,DIRECT", "enabled": True}, n["policies"][2]]
        self.assertEqual(len(rule_groups(n)), 5)
        n["policies"][2]["text"] = "AND,((RULE-SET,one),(NETWORK,udp)),Media"
        self.assertEqual(len(rule_groups(n)), 5)
        before = deepcopy(n)
        checked(n)
        self.assertEqual(n, before)

    def test_preview_is_read_only_and_failed_source_does_not_write_outlet(self):
        n = grouped_fixture()
        original = deepcopy(n)
        request = edit_request(n, "ai", outlet={"type": "select", "members": ["JP"], "default": "JP"})
        request["sources"] = [{"name": "fresh", "url": "https://example.test/rules.yaml"}]
        with patch("yomesh.native_configuration.refresh_provider") as fetch:
            result = preview(n, request)
            fetch.assert_not_called()
        self.assertIn("fresh", result["text"])
        self.assertEqual(n, original)
        with patch("yomesh.native_configuration.refresh_provider", side_effect=ConfigError("source failed")):
            with self.assertRaises(ConfigError):
                change(n, request)
        self.assertEqual(n, original)
