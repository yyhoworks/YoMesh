"""Integration checks for native interactions and the semantics they must preserve."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.compiler import compile_bundle
from yomesh.interaction_tools import node_catalog, preview_group, parse_import, diagnose, repair_reference, wait_native_ready
from yomesh.interaction_service import InteractionService
from yomesh.model import ConfigError
from yomesh.native import validate_bundle
from yomesh.native_rule_groups import change, rule_groups
from yomesh.native_rule_conditions import condition_editor
from yomesh.spaces import ConfigurationSpaces
from yomesh.workspace import Conflict


def native():
    n = fixture()
    n["format"] = "mihomo-v1"
    n["mihomo"]["proxies"] = [
        {"name": "日本 Tokyo 01", "type": "ss", "server": "127.0.0.1", "port": 9, "cipher": "aes-128-gcm", "password": "test-fixture-only"},
        {"name": "日本 高倍率", "type": "ss", "server": "127.0.0.1", "port": 9, "cipher": "aes-128-gcm", "password": "test-fixture-only"},
        {"name": "香港 01", "type": "socks5", "server": "127.0.0.1", "port": 9},
    ]
    n["mihomo"]["proxy-providers"] = {"手动": {"type": "file", "path": "providers/one.yaml"}}
    n["documents"]["providers/one.yaml"] = "proxies:\n- name: 日本 Provider\n  type: ss\n  server: 127.0.0.1\n  port: 9\n  cipher: aes-128-gcm\n  password: test-fixture-only\n"
    return n


class NativeInteractionTests(unittest.TestCase):
    def test_controller_ready_does_not_mean_provider_cache_is_ready(self):
        calls = []
        def api(path):
            if path == '/proxies':
                return {'proxies': {'group': {}}}
            calls.append(path)
            return {'providers': {'source': {'updatedAt': '0001-01-01T00:00:00Z' if len(calls) == 1 else '2026-10-20T00:00:00Z'}}}
        wait_native_ready(api, {'proxy-groups': [{'name': 'group'}], 'proxy-providers': {'source': {}}}, timeout=1)
        self.assertGreaterEqual(len(calls), 2)

    def test_core_preview_uses_real_provider_filters_and_does_not_mutate(self):
        n = native()
        original = deepcopy(n)
        request = {"group": {"name": "测试筛选", "type": "select", "use": ["手动"], "proxies": [p["name"] for p in n["mihomo"]["proxies"]]}, "filters": {"region": "JP", "protocol": "ss", "exclude": "高倍率", "regex": ""}}
        result = preview_group(ROOT, n, request)
        # Native filter does not affect explicit members (Compatible provider),
        # matching Mihomo rather than pretending every member was filtered.
        self.assertIn("日本 Provider", [m["name"] for m in result["members"]])
        self.assertEqual(n, original)
        self.assertEqual(result["method"], "mihomo-1.19.32")

    def test_provider_preview_filters_country_and_exclusions_natively(self):
        n = native()
        n["documents"]["providers/one.yaml"] += "- name: 香港 Provider\n  type: ss\n  server: 127.0.0.1\n  port: 9\n  cipher: aes-128-gcm\n  password: test-fixture-only\n"
        result = preview_group(ROOT, n, {"group": {"name": "筛选", "type": "select", "use": ["手动"]}, "filters": {"region": "JP", "protocol": "ss", "exclude": "高倍率", "regex": "Provider"}})
        self.assertEqual([m["name"] for m in result["members"]], ["日本 Provider"])

    def test_empty_native_group_reports_zero_members_not_a_synthetic_node(self):
        result = preview_group(ROOT, native(), {"group": {"name": "空筛选", "type": "select", "use": ["手动"], "filter": "NO-MATCH-NODE"}})
        self.assertEqual(result["count"], 0)
        self.assertTrue(result["fallback"])
        self.assertEqual(result["members"], [])

    def test_isolated_preview_does_not_change_candidate_health_settings(self):
        request = {"group": {"name": "auto", "type": "url-test", "use": ["手动"], "url": "https://www.gstatic.com/generate_204", "interval": 123, "lazy": False}}
        before = deepcopy(request)
        result = preview_group(ROOT, native(), request)
        self.assertEqual(request, before)
        self.assertEqual(result["group"], before["group"])

    def test_inline_provider_nodes_are_visible_and_use_native_membership(self):
        n = native()
        node = {**n["mihomo"]["proxies"][0], "name": "Inline Test"}
        n["mihomo"]["proxy-providers"]["内联"] = {"type": "inline", "payload": [node], "health-check": {"enable": True, "url": "http://127.0.0.1:9", "interval": 1}}
        from yomesh.native_configuration import overview, cached_proxy_names
        rows = overview(n)["nodes"]
        self.assertIn('Inline Test', cached_proxy_names(n))
        self.assertEqual(next(r for r in rows if r['name'] == 'Inline Test')['origin'], 'subscription')
        result = preview_group(ROOT, n, {'group': {'name': 'inline-group', 'type': 'select', 'use': ['内联']}})
        self.assertEqual([r['name'] for r in result['members']], ['Inline Test'])
        self.assertTrue(n['mihomo']['proxy-providers']['内联']['health-check']['enable'])

    def test_node_ids_do_not_expose_credentials_and_change_when_credentials_change(self):
        n = native()
        rows = node_catalog(n)
        self.assertEqual(rows[-1]["origin"], "subscription")
        self.assertNotIn("password", json.dumps(rows))
        before = rows[0]["id"]
        n["mihomo"]["proxies"][0]["password"] = "changed"
        self.assertNotEqual(before, node_catalog(n)[0]["id"])

    def test_native_import_preserves_unknown_fields_and_rejects_lossy_uri(self):
        text = json.dumps({"name": "test", "type": "wireguard", "server": "127.0.0.1", "port": 9, "future-option": {"x": 1}, "private-key": "private-test"})
        self.assertEqual(parse_import(text)["nodes"][0]["future-option"], {"x": 1})
        with self.assertRaises(ConfigError):
            parse_import("vless://id@example.com:443?unrecognized-option=value")
        config = {"rules": ["MATCH,DIRECT"], "proxies": [], "future-field": {"enabled": True}, "dns": {"enable": False}}
        parsed = parse_import(json.dumps(config))
        self.assertEqual(parsed["configuration"]["mihomo"]["future-field"], config["future-field"])

    def test_multiline_condition_keeps_or_inside_and_and_passes_native_check(self):
        n = native()
        draft = {"mode": "conditions", "operator": "and", "name": "多域名 UDP", "target": "DIRECT", "conditions": [{"type": "DOMAIN-SUFFIX", "value": "one.test\ntwo.test"}, {"type": "NETWORK", "value": "udp"}]}
        result = change(n, {"action": "unit-save", "draft": draft, "unit": True})
        text = next(p["text"] for p in result["policies"] if p.get("name") == "多域名 UDP")
        self.assertEqual(text, "AND,((OR,((DOMAIN-SUFFIX,one.test),(DOMAIN-SUFFIX,two.test))),(NETWORK,udp)),DIRECT")
        editor = condition_editor(text)
        self.assertEqual(editor["conditions"][0]["value"], "one.test\ntwo.test")
        self.assertEqual(editor["operator"], "and")
        bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": result}, "mihomo", rule_output="remote", component="proxy")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_multiline_rejects_bad_lines_and_duplicate_values(self):
        for value in ("example.com\nhttps://wrong/path", "example.com\nexample.com"):
            with self.assertRaises(ConfigError):
                change(native(), {"action": "unit-save", "unit": True, "draft": {"mode": "conditions", "target": "DIRECT", "name": "bad", "conditions": [{"type": "DOMAIN-SUFFIX", "value": value}]}})

    def test_diagnosis_unknown_earlier_rule_blocks_false_definitive_result(self):
        n = native()
        n["policies"].insert(0, {"id": "geo", "enabled": True, "text": "GEOIP,CN,DIRECT"})
        result = diagnose(n, "example.com")
        self.assertIsNotNone(result["match"])
        self.assertFalse(result["definitive"])
        self.assertEqual(result["path"][0]["state"], "unknown")

    def test_match_duplicate_missing_and_disabled_and_rule(self):
        n = native()
        n["policies"].insert(0, {"id": "and", "name": "broken", "enabled": False, "text": "AND,((RULE-SET,missing),(NETWORK,udp)),DIRECT"})
        n["policies"].insert(2, {"id": "dupe", "enabled": True, "text": "RULE-SET,one,DIRECT"})
        result = diagnose(n, "example.com")
        self.assertTrue(result["definitive"])
        self.assertEqual(result["missing"][0]["ref"], "missing")
        self.assertEqual(len(result["duplicates"]), 1)
        repaired = repair_reference(n, {"id": "and", "ref": "missing", "operation": "replace", "replacement": "one"})
        self.assertEqual(repaired["policies"][0]["text"], "AND,((RULE-SET,one),(NETWORK,udp)),DIRECT")
        self.assertFalse(repaired["policies"][0]["enabled"])
        disabled = repair_reference(n, {"id": "and", "ref": "missing", "operation": "disable"})
        self.assertEqual(disabled["policies"][0]["text"], n["policies"][0]["text"])

    def test_unsupported_binary_cache_never_claims_a_match(self):
        n = native()
        n["mihomo"]["rule-providers"]["one"] = {"type": "file", "path": "rules/one.mrs", "format": "mrs", "behavior": "domain"}
        result = diagnose(n, "example.com")
        self.assertFalse(result["definitive"])
        self.assertEqual(result["path"][0]["state"], "unknown")

    def test_explicit_protocol_and_port_context_can_resolve_earlier_conditions(self):
        n = native()
        n["policies"].insert(0, {"id": "port", "enabled": True, "text": "DST-PORT,19302,REJECT"})
        self.assertFalse(diagnose(n, "example.com")["definitive"])
        result = diagnose(n, "example.com", {"network": "tcp", "port": 443})
        self.assertTrue(result["definitive"])
        self.assertEqual(result["match"]["id"], "first")
        self.assertEqual(diagnose(n, "example.com", {"port": 19302})["match"]["target"], "REJECT")


class SharedAndImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.spaces = ConfigurationSpaces(self.root)
        self.service = InteractionService(ROOT, self.spaces)
        n = native()
        n["mihomo"]["rule-providers"]["shared"] = {"type": "http", "url": "https://rules.test/shared.yaml", "path": "rules/shared.yaml", "behavior": "domain"}
        n["documents"]["rules/shared.yaml"] = "payload: [example.com]\n"
        self.a = self.spaces.import_native(n, "A", self.spaces.state["revision"])["id"]
        self.b = self.spaces.import_native(native(), "B", self.spaces.state["revision"])["id"]

    def tearDown(self):
        self.spaces.close()
        self.temp.cleanup()

    def share(self, mode):
        found = self.service.handle(self.b, {"action": "resource-find", "url": "https://rules.test/shared.yaml", "section": "rule-providers"})
        item = found["matches"][0]
        return self.service.handle(self.b, {"action": "resource-apply", "mode": mode, "section": "rule-providers", "source_space": self.a, "source_name": "shared", "name": "shared", "source_revision": item["revision"], "catalog_revision": found["catalog_revision"], "revision": self.spaces.runtime(self.b).workspace.revision()})

    def test_shared_change_is_explicit_persists_and_default_edit_detaches(self):
        self.share("share")
        snap = self.spaces.workspace_snapshot(self.a)
        snap["profile"]["native_configuration"]["documents"]["rules/shared.yaml"] = "payload: [updated.test]\n"
        self.spaces.save_profile(snap["profile"], snap["revision"], self.a, "shared")
        self.assertIn("updated.test", self.spaces.workspace_snapshot(self.b)["profile"]["native_configuration"]["documents"]["rules/shared.yaml"])
        self.spaces.close()
        self.spaces = ConfigurationSpaces(self.root)
        self.assertIn("updated.test", self.spaces.workspace_snapshot(self.b)["profile"]["native_configuration"]["documents"]["rules/shared.yaml"])
        snap = self.spaces.workspace_snapshot(self.b)
        snap["profile"]["native_configuration"]["documents"]["rules/shared.yaml"] = "payload: [only-b.test]\n"
        self.spaces.save_profile(snap["profile"], snap["revision"], self.b)
        self.assertNotIn("only-b.test", self.spaces.workspace_snapshot(self.a)["profile"]["native_configuration"]["documents"]["rules/shared.yaml"])
        self.assertIn("only-b.test", self.spaces.workspace_snapshot(self.b)["profile"]["native_configuration"]["documents"]["rules/shared.yaml"])

    def test_copy_is_independent_and_stale_source_is_rejected(self):
        self.share("copy")
        self.assertFalse(self.spaces.state.get("native_resources"))
        snapshot = self.spaces.workspace_snapshot(self.b)
        self.assertIn("shared", snapshot["profile"]["native_configuration"]["mihomo"]["rule-providers"])
        with self.assertRaises(Conflict):
            self.service.handle(self.b, {"action": "resource-apply", "mode": "share", "section": "rule-providers", "catalog_revision": -1})

    def test_shared_save_rolls_back_catalog_and_profile_after_disk_failure(self):
        self.share("share")
        before_a = self.spaces.workspace_snapshot(self.a)
        before_b = self.spaces.workspace_snapshot(self.b)
        candidate = deepcopy(before_a["profile"])
        candidate["native_configuration"]["documents"]["rules/shared.yaml"] = "payload: [failed.test]\n"
        with patch.object(self.spaces, "_write", side_effect=OSError("test disk failure")):
            with self.assertRaises(OSError):
                self.spaces.save_profile(candidate, before_a["revision"], self.a, "shared")
        self.assertEqual(self.spaces.workspace_snapshot(self.a)["profile"], before_a["profile"])
        self.assertEqual(self.spaces.workspace_snapshot(self.b)["profile"], before_b["profile"])
        self.assertFalse((self.root / "data/.resource-transaction.json").exists())
        self.spaces.close()
        self.spaces = ConfigurationSpaces(self.root)
        self.assertEqual(self.spaces.workspace_snapshot(self.a)["profile"], before_a["profile"])

    def test_import_preview_duplicate_default_and_commit_scope_and_revision(self):
        snapshot = self.spaces.workspace_snapshot(self.b)
        value = {"name": "new", "type": "socks5", "server": "127.0.0.1", "port": 9, "unknown-native-option": True}
        req = {"action": "import-preview", "text": json.dumps({"proxies": [value, value]}), "revision": snapshot["revision"]}
        result = self.service.handle(self.b, req)
        self.assertFalse(result["items"][0]["duplicate"])
        self.assertTrue(result["items"][1]["duplicate"])
        self.assertNotIn("nodes", result)
        with self.assertRaises(ConfigError):
            self.service.handle(self.a, {"action": "import-commit", "revision": self.spaces.runtime(self.a).workspace.revision(), "token": result["token"], "indices": [0]})
        changed = self.service.handle(self.b, {"action": "import-commit", "revision": snapshot["revision"], "token": result["token"], "indices": [0]})
        self.assertTrue(changed["workspace"]["profile"]["native_configuration"]["mihomo"]["proxies"][-1]["unknown-native-option"])

    def test_real_native_probe_records_failure_without_removing_nodes(self):
        runtime = self.spaces.runtime(self.b)
        snapshot = self.spaces.workspace_snapshot(self.b)
        before = deepcopy(snapshot["profile"])
        ident = node_catalog(before["native_configuration"])[0]["id"]
        self.service.handle(self.b, {"action": "test-nodes", "revision": snapshot["revision"], "ids": [ident], "timeout": 1000})
        deadline = time.monotonic() + 15
        while runtime.subscriptions.busy("native_nodes") and time.monotonic() < deadline:
            time.sleep(.05)
        after = runtime.workspace.snapshot()
        self.assertEqual(after["group_status"]["native_nodes"]["test_state"], "success")
        self.assertEqual(after["node_status"][ident]["state"], "failed")
        self.assertEqual(before, after["profile"])

    def test_explicit_rule_scan_is_bounded_and_releases_slot_after_error(self):
        request = {"action": "diagnose", "scan": True}
        with self.service.scan_slot:
            with self.assertRaises(ConfigError):
                self.service.handle(self.b, request)
        with patch("yomesh.interaction_service.scan_rule_entries", side_effect=ConfigError("test cache error")):
            with self.assertRaises(ConfigError):
                self.service.handle(self.b, request)
        result = self.service.handle(self.b, request)
        self.assertIn("entry_scan", result)
        self.assertNotIn("entry_scan", self.service.handle(self.b, {"action": "diagnose"}))


if __name__ == '__main__':
    unittest.main()
