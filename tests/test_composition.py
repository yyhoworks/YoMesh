import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.composition import resolve_composition, preview_composition
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))

    def test_disabled_rule_skips_whole_and_and_or_policy_for_all_targets(self):
        rule = self.value["library"][0]["id"]
        other = self.value["library"][1]["id"]
        for operator in ("and", "or"):
            value = deepcopy(self.value)
            value.pop("routing_plans", None);value.pop("active_plan", None);value["inbounds"] = []
            value["policies"] = [{"id": "test", "name": "组合", "type": "LOGICAL", "target": "DIRECT", "condition_groups": [{"operator": operator, "conditions": [{"type": "RULE-SET", "value": rule}, {"type": "RULE-SET", "value": other}]}]}]
            value["composition"] = {"rules": [other]}
            for target in ("mihomo", "sing-box"):
                bundle = compile_bundle(value, target)
                self.assertEqual(bundle["manifest"]["composition"]["policies"], [])
                self.assertEqual(bundle["manifest"]["composition"]["excluded_policies"][0]["id"], "test")
                validate_bundle(bundle, ROOT)

    def test_selected_groups_prune_unused_nodes_and_missing_target_is_blocked(self):
        value = deepcopy(self.value)
        group = value["groups"][0]
        value["policies"] = [];value["routing_plans"] = [];value.pop("active_plan", None);value["inbounds"] = []
        value["composition"] = {"groups": [group["id"]], "rules": [], "policies": [], "fallback": group["id"]}
        resolved, info = resolve_composition(value)
        self.assertIn(group["id"], info["groups"])
        self.assertLessEqual(len(resolved["nodes"]), len(value["nodes"]))
        for target in ("mihomo", "sing-box"):
            validate_bundle(compile_bundle(value, target, rule_output="local"), ROOT)
        value["composition"]["groups"] = []
        with self.assertRaisesRegex(ConfigError, "未选中的节点组"):
            compile_bundle(value)

    def test_composition_preserves_original_and_rejects_missing_ids(self):
        value = deepcopy(self.value);value["composition"] = {"rules": []}
        before = deepcopy(value)
        compile_bundle(value)
        self.assertEqual(value, before)
        value["composition"] = {"groups": ["unknown"]}
        with self.assertRaises(ConfigError):
            compile_bundle(value)

    def test_preview_keeps_exclusions_when_an_outbound_is_missing_and_recommends_dependencies(self):
        value = deepcopy(self.value)
        value["composition"] = {"groups": [], "rules": ["l1"], "policies": ["p2", "p4"], "fallback": "DIRECT"}
        before = deepcopy(value)
        result = preview_composition(value)
        self.assertFalse(result["valid"])
        self.assertIn("未选中的节点组", result["error"])
        self.assertEqual([p["id"] for p in result["excluded_policies"]], ["p2"])
        recommended = result["recommended_selection"]
        self.assertIn("g2", recommended["groups"])
        self.assertIn("g1", recommended["groups"])
        self.assertIn("l2", recommended["rules"])
        value["composition"] = recommended
        resolved, _ = resolve_composition(value)
        checked = preview_composition(value)
        self.assertTrue(checked["valid"], checked["error"])
        self.assertEqual(checked["counts"]["policies"], len(resolved["policies"]))
        self.assertEqual(before["composition"]["rules"], ["l1"])

    def test_preview_includes_inbound_dependencies_and_ignores_unselected_policies(self):
        value = deepcopy(self.value)
        value["active_plan"] = "main"
        value["routing_plans"] = [{"id": "main", "name": "main", "policies": deepcopy(value["policies"]), "fallback": "DIRECT"}, {"id": "bound", "name": "bound", "policies": [{"id": "bound-policy", "name": "入站", "type": "RULE-SET", "value": "l2", "target": "g2", "enabled": True}], "fallback": "DIRECT"}]
        value["inbounds"] = [{"id": "in", "name": "绑定", "protocol": "mixed", "listen": "127.0.0.1", "port": 19011, "plan": "bound", "enabled": True}]
        value["composition"] = {"plan": "main", "groups": [], "rules": [], "policies": [], "fallback": "DIRECT"}
        before = deepcopy(value)
        result = preview_composition(value)
        self.assertEqual([r["policy"] for r in result["rule_impacts"]["l2"]], ["bound-policy"])
        self.assertEqual(result["rule_impacts"]["l1"], [])
        self.assertIn("l2", result["recommended_selection"]["rules"])
        self.assertIn("g2", result["recommended_selection"]["groups"])
        self.assertEqual(value, before)
