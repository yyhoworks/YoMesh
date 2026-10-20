from copy import deepcopy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle


class CompilerTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))

    def test_every_native_target_loads_all_five_protocols(self):
        for target in ("mihomo", "sing-box"):
            with self.subTest(target=target):
                bundle = compile_bundle(self.value, target)
                self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_singbox_fallback_never_silently_changes_to_urltest(self):
        self.value["groups"][0]["mode"] = "fallback"
        with self.assertRaisesRegex(ConfigError, "无原生 fallback 等价实现"):
            compile_bundle(self.value, "sing-box")
        self.assertEqual(compile_bundle(self.value, "mihomo")["files"]["mihomo.yaml"]["proxy-groups"][0]["type"], "fallback")

    def test_disabled_referenced_node_is_rejected(self):
        self.value["nodes"][1]["enabled"] = False
        with self.assertRaisesRegex(ConfigError, "出口不存在或已停用"):
            compile_bundle(self.value)

    def test_missing_credentials_are_not_replaced_by_fake_values(self):
        del self.value["nodes"][0]["password"]
        with self.assertRaisesRegex(ConfigError, "password"):
            compile_bundle(self.value)

    def test_unknown_protocol_fields_are_rejected(self):
        self.value["nodes"][0]["plugin"] = "not-yet-supported"
        with self.assertRaisesRegex(ConfigError, "未支持的节点字段"):
            compile_bundle(self.value)

    def test_cycle_rejected_before_native_check(self):
        self.value["groups"][0]["groups"] = ["g2"]
        with self.assertRaisesRegex(ConfigError, "循环引用"):
            compile_bundle(self.value)

    def test_rule_order_reject_and_final_semantics(self):
        mihomo = compile_bundle(self.value)["files"]["mihomo.yaml"]
        singbox = compile_bundle(self.value, "sing-box")["files"]["sing-box.json"]
        self.assertTrue(mihomo["rules"][0].startswith("IP-CIDR,10.0.0.0/8,DIRECT"))
        self.assertEqual(mihomo["rules"][-1], "MATCH,group_g2")
        self.assertEqual(singbox["route"]["final"], "group_g2")
        reject = next(r for r in singbox["route"]["rules"] if r.get("action") == "reject")
        self.assertEqual(reject["domain_suffix"], ["ads.example"])
        self.assertFalse(any(n["type"] == "block" for n in singbox["outbounds"]))

    def test_shared_member_resolution_and_dns_scope(self):
        self.value["groups"][0]["include"] = "Trojan|VMess"
        bundle = compile_bundle(self.value)
        self.assertEqual(bundle["files"]["mihomo.yaml"]["proxy-groups"][0]["proxies"], ["node_n2", "node_n3"])
        self.assertEqual(bundle["manifest"]["diagnostics"][0]["policy"], "p1")
        self.assertEqual(bundle["manifest"]["diagnostics"][0]["scope"], "proxy")

    def test_grpc_and_reality_native_formats(self):
        # X25519 public key for a structural fixture, not a reachable server.
        n = self.value["nodes"][3]
        n["tls"]["reality"] = {"public_key": "VD-RrMXwHhRLGdB7vfKCWXlsMivNSDeV1bR2NNnhrUI", "short_id": "12345678"}
        n["flow"] = "xtls-rprx-vision"
        self.value["nodes"][1]["transport"] = {"type": "grpc", "service_name": "test"}
        for target in ("mihomo", "sing-box"):
            with self.subTest(target=target):
                self.assertEqual(validate_bundle(compile_bundle(self.value, target), ROOT)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
