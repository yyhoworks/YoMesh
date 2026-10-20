"""Semantics checks for filter round-trips, cache inspection and probe history."""
from copy import deepcopy
import base64
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from test_interaction_tools import native
from yomesh.interaction_tools import group_candidate, group_filter_view, diagnose
from yomesh.interaction_inspection import scan_rule_entries
from yomesh.interaction_probe import measure_node, record_probe_result
from yomesh.workspace import Workspace


class FilterRoundTripTests(unittest.TestCase):
    def test_imported_region_is_shown_and_unchanged_filter_stays_exact(self):
        group = {"name": "Japan", "type": "select", "filter": "🇯🇵|JP|JPN|日本|Japan|东京", "future": {"enabled": True}}
        view = group_filter_view(group)
        self.assertEqual((view["region"], view["regex"]), ("JP", ""))
        result = group_candidate(native(), {"group": group, "filters": {"region": view["region"], "regex": view["regex"], "replace_known": True}})
        self.assertEqual(result, group)
        result = group_candidate(native(), {"group": group, "filters": {"region": "HK", "regex": "", "replace_known": True}})
        self.assertNotIn("日本", result["filter"])
        self.assertEqual(result["future"], group["future"])

    def test_generated_region_and_regex_do_not_accumulate_on_reopen(self):
        group = {"name": "test", "type": "select"}
        filters = {"region": "JP", "protocol": "ss", "regex": "Provider", "replace_known": True}
        group = group_candidate(native(), {"group": group, "filters": filters})
        view = group_filter_view(group)
        self.assertEqual((view["region"], view["regex"], view["protocol"]), ("JP", "Provider", "ss"))
        self.assertEqual(group_candidate(native(), {"group": group, "filters": filters}), group)
        filters["protocol"] = ""
        self.assertNotIn("exclude-type", group_candidate(native(), {"group": group, "filters": filters}))

    def test_custom_conditions_never_disappear_or_get_guessed_as_region(self):
        group = {"name": "test", "type": "select", "filter": ".*(美国.*DMIT|DMIT.*美国).*", "exclude-filter": "private.*", "exclude-type": "Vless"}
        view = group_filter_view(group)
        self.assertEqual(view["region"], "")
        self.assertTrue(view["custom_include"])
        self.assertTrue(view["custom_protocol"])
        result = group_candidate(native(), {"group": group, "filters": {"region": "", "regex": view["regex"], "replace_known": True}})
        self.assertEqual(result, group)


class RuleCacheInspectionTests(unittest.TestCase):
    def test_internal_cross_provider_and_direct_duplicates_with_positions(self):
        n = native()
        n["mihomo"]["rule-providers"] = {
            "a": {"type": "inline", "behavior": "domain", "payload": ["+.example.com", "+.example.com"]},
            "b": {"type": "file", "behavior": "classical", "path": "rules/b.txt", "format": "text"},
        }
        n["documents"]["rules/b.txt"] = "# comment\nDOMAIN-SUFFIX,example.com\nDOMAIN-SUFFIX,other.test\n"
        n["policies"] = [{"id": "direct", "text": "DOMAIN-SUFFIX,example.com,DIRECT", "enabled": True}, {"id": "ref", "text": "RULE-SET,a,DIRECT", "enabled": True}, {"id": "fallback", "text": "MATCH,DIRECT", "enabled": True}]
        before = deepcopy(n)
        result = scan_rule_entries(n)
        self.assertEqual(result["duplicate_count"], 1)
        group = result["groups"][0]
        self.assertEqual(group["count"], 4)
        self.assertEqual([r["position"] for r in group["occurrences"]], [1, 1, 2, 1])
        self.assertEqual(result["scanned_providers"], 2)
        self.assertEqual(n, before)
        self.assertTrue(result["complete"])

    def test_logic_children_and_no_resolve_are_not_false_duplicates(self):
        n = native()
        n["policies"] = [{"id": "logic", "text": "AND,((DOMAIN,example.com),(NETWORK,udp)),DIRECT", "enabled": True}, {"id": "direct", "text": "IP-CIDR,10.0.0.0/8,DIRECT,no-resolve", "enabled": True}]
        n["mihomo"]["rule-providers"] = {"p": {"type": "inline", "behavior": "classical", "payload": ["DOMAIN,example.com", "IP-CIDR,10.0.0.0/8"]}}
        self.assertEqual(scan_rule_entries(n)["duplicate_count"], 0)

    def test_shared_provider_scanned_once_and_missing_binary_coverage_explicit(self):
        n = native()
        n["mihomo"]["rule-providers"] = {"p": {"type": "inline", "behavior": "domain", "payload": ["example.com"]}, "binary": {"type": "file", "format": "mrs", "path": "rules/b.mrs"}}
        n["policies"] = [{"id": str(i), "text": "RULE-SET,p,DIRECT", "enabled": True} for i in range(2)]
        result = scan_rule_entries(n)
        self.assertFalse(result["complete"])
        self.assertEqual(result["skipped"][0]["name"], "binary")
        self.assertNotIn("DOMAIN,example.com", [r["expression"] for r in result["groups"]])
        self.assertEqual(scan_rule_entries(n, limit=1)["scanned_entries"], 1)

    def test_text_rule_cache_is_used_by_same_diagnosis_path(self):
        n = native()
        n["mihomo"]["rule-providers"]["one"] = {"type": "file", "behavior": "domain", "format": "text", "path": "rules/text.txt"}
        n["documents"]["rules/text.txt"] = "# header\n+.example.com\n"
        self.assertTrue(diagnose(n, "example.com")["definitive"])

    def test_original_yaml_archived_as_asset_is_read_without_rewriting(self):
        n = native()
        n["mihomo"]["rule-providers"]["one"] = {"type": "file", "behavior": "classical", "format": "yaml", "path": "rules/original.yaml"}
        n.setdefault("assets", {})["rules/original.yaml"] = base64.b64encode(b"rules:\n- DOMAIN,example.com,DIRECT\n- DOMAIN,example.com,DIRECT\n").decode()
        before = deepcopy(n)
        self.assertTrue(diagnose(n, "example.com")["definitive"])
        self.assertEqual(scan_rule_entries(n)["groups"][0]["count"], 2)
        self.assertEqual(before, n)

    def test_classical_logic_with_action_compares_whole_condition_and_diagnoses(self):
        n = native()
        expression = "AND,((DOMAIN,example.com),(NETWORK,udp))"
        n["mihomo"]["rule-providers"] = {"p": {"type": "inline", "behavior": "classical", "payload": [expression + ",REJECT"]}}
        n["policies"] = [{"id": "provider", "enabled": True, "text": "RULE-SET,p,DIRECT"}, {"id": "direct", "enabled": True, "text": expression + ",DIRECT"}, {"id": "fallback", "enabled": True, "text": "MATCH,DIRECT"}]
        self.assertEqual(scan_rule_entries(n)["groups"][0]["expression"], expression)
        result = diagnose(n, "example.com", {"network": "udp"})
        self.assertTrue(result["definitive"])
        self.assertEqual(result["match"]["id"], "provider")


class NativeProbeDetailsTests(unittest.TestCase):
    row = {"id": "test", "raw": {"type": "ss", "server": "private-node.test", "port": 443}}

    def test_failure_classification_does_not_expose_core_message_or_target_token(self):
        def core(*args):
            raise HTTPError("http://controller", 503, "bad", {}, io.BytesIO(b'{"message":"x509 certificate failed secret-password"}'))
        result = measure_node(core, self.row, "https://test.example/probe?token=private", 1000)
        self.assertEqual(result["error_code"], "tls")
        self.assertNotIn("secret-password", json.dumps(result))
        self.assertNotIn("token", json.dumps(result))
        self.assertEqual(result["test_target"], "test.example")

    def test_generic_failure_checks_tcp_but_udp_and_chains_are_not_misdiagnosed(self):
        def core(*args):
            raise ValueError()
        with patch("yomesh.interaction_probe.socket.create_connection", side_effect=ConnectionRefusedError()):
            result = measure_node(core, self.row, "https://test.example", 1000)
            self.assertEqual(result["transport"], "tcp_refused")
        with patch("yomesh.interaction_probe.socket.create_connection") as connect:
            for raw, expected in [({"type": "hysteria2"}, "not_checked_udp"), ({"type": "ss", "dialer-proxy": "chain"}, "not_checked_chain")]:
                result = measure_node(core, {"id": "a", "raw": raw}, "https://test.example", 1000)
                self.assertEqual(result["transport"], expected)
            connect.assert_not_called()

    def test_history_is_bounded_persists_and_never_changes_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            before = deepcopy(workspace.state["profile"])
            workspace.state["node_status"] = {"old": {"tested_at": 1}, "node": {"state": "failed", "tested_at": 2}}
            record_probe_result(workspace, "node", {"state": "success", "latency_ms": 10, "tested_at": 3}, {"node"})
            self.assertEqual(len(workspace.state["node_status"]["node"]["history"]), 2)
            for i in range(4, 26):
                record_probe_result(workspace, "node", {"state": "success", "latency_ms": i, "tested_at": i}, {"node"})
            workspace.probe_status("native_nodes", {"test_state": "success"})
            restored = Workspace(directory)
            self.assertEqual(len(restored.state["node_status"]["node"]["history"]), 14)
            self.assertEqual(restored.state["node_status"]["node"]["history"][0]["tested_at"], 25)
            self.assertNotIn("old", restored.state["node_status"])
            self.assertEqual(restored.state["profile"], before)


if __name__ == "__main__":
    unittest.main()
