"""Base documents preserve generated routing and load in the locked native cores."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.base_config import parse_base, base_editor, prepare_base_profile
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError, Profile
from yomesh.native import validate_bundle
from yomesh.workspace import Workspace, Conflict


class BaseConfigTests(unittest.TestCase):
    def test_native_base_editor_keeps_both_targets_and_saves_separate_template(self):
        from test_native_configuration import fixture
        from yomesh.spaces import ConfigurationSpaces
        n = fixture()
        with tempfile.TemporaryDirectory() as directory:
            spaces = ConfigurationSpaces(directory)
            try:
                result = spaces.import_native(n, "fixture", spaces.state["revision"])
                ident = result["id"]
                before = spaces.workspace_snapshot(ident)
                templates = base_editor(Profile({"schema_version": 1, "fallback": "DIRECT"}), n)
                self.assertTrue(templates["native"])
                self.assertEqual(set(templates["defaults"]), {"mihomo", "sing-box"})
                text = '{"log":{"level":"warn"}}'
                after = spaces.save_base_config("sing-box", text, before["revision"], ident)
                self.assertEqual(after["profile"]["native_configuration"], n)
                self.assertEqual(after["profile"]["base_configs"]["sing-box"], text)
                result = spaces.save_base_config("mihomo", templates["stored"]["mihomo"] + "log-level: warning\n", after["revision"], ident)
                actual = compile_bundle(result["profile"], rule_output="remote", component="proxy")
                self.assertIn("log-level: warning", actual["files"]["mihomo.yaml"])
                self.assertEqual(result["profile"]["native_configuration"]["policies"], n["policies"])
                self.assertEqual(validate_bundle(actual, ROOT)["status"], "passed")
            finally:
                spaces.close()

    def test_native_base_preserves_unknown_fields_and_protects_managed_resources(self):
        from test_native_configuration import fixture
        value = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": fixture()}
        changed = prepare_base_profile(value, "mihomo", "mixed-port: 17890\nfuture-native-field: {preserve: true}\n")
        self.assertEqual(changed["native_configuration"]["mihomo"]["future-native-field"], {"preserve": True})
        for section in ("proxies", "proxy-groups", "rule-providers", "listeners"):
            self.assertEqual(changed["native_configuration"]["mihomo"][section], value["native_configuration"]["mihomo"][section])
        for text in ("", "rules: []", "mixed-port: 17890\nmixed-port: 18890", "[]"):
            with self.assertRaises(ConfigError):
                prepare_base_profile(value, "mihomo", text)

    def setUp(self):
        self.value = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))

    def test_both_default_templates_merge_and_load_natively(self):
        defaults = base_editor(Profile(self.value))["defaults"]
        for target in ("mihomo", "sing-box"):
            value = deepcopy(self.value)
            value["base_configs"] = {target: defaults[target]}
            bundle = compile_bundle(value, target)
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
            self.assertTrue(bundle["manifest"]["base_config"]["custom"])

    def test_mihomo_custom_ports_dns_sniffer_tun_and_generated_rules_are_preserved(self):
        value = deepcopy(self.value)
        value["base_configs"] = {"mihomo": "# retained source comment\nmixed-port: 18891\nsocks-port: 18892\nallow-lan: false\nipv6: false\nlog-level: warning\nunified-delay: true\ntcp-concurrent: true\ndns:\n  ipv6: false\nprofile:\n  store-selected: true\ntun:\n  enable: false\nsniffer:\n  enable: true\n  sniff:\n    HTTP:\n      ports: ['80', '8080']\n"}
        before = deepcopy(value)
        bundle = compile_bundle(value)
        native = bundle["files"]["mihomo.yaml"]
        baseline = compile_bundle(self.value)["files"]["mihomo.yaml"]
        for key in ("proxies", "proxy-groups", "rules"):
            self.assertEqual(native[key], baseline[key])
        self.assertEqual((native["mixed-port"], native["socks-port"]), (18891, 18892))
        self.assertFalse(native["dns"]["ipv6"])
        self.assertEqual(native["log-level"], "warning")
        self.assertIn("tun", native)
        self.assertEqual(next(p for p in bundle["files"]["mosdns.yaml"]["plugins"] if p["tag"] == "remote_dns")["args"]["socks5"], "127.0.0.1:18892")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        self.assertEqual(value, before)
        value["base_configs"] = {"mihomo": "socks-port: 18892"}
        socks_only = compile_bundle(value)
        self.assertEqual(socks_only["files"]["mihomo.yaml"]["mixed-port"], self.value.get("settings", {}).get("mixed_port", 17890))
        self.assertEqual(validate_bundle(socks_only, ROOT)["status"], "passed")

    def test_singbox_base_inbounds_route_and_experimental_options_merge_and_load(self):
        value = deepcopy(self.value)
        value["base_configs"] = {"sing-box": json.dumps({"log": {"level": "warn"}, "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": 18893}], "route": {"auto_detect_interface": True}, "experimental": {"clash_api": {"external_controller": "127.0.0.1:19090", "secret": "fixture-test"}}})}
        bundle = compile_bundle(value, "sing-box")
        native = bundle["files"]["sing-box.json"]
        self.assertEqual(native["inbounds"][0]["listen_port"], 18893)
        self.assertTrue(native["route"]["auto_detect_interface"])
        self.assertTrue(native["route"]["rules"])
        self.assertEqual(native["experimental"]["clash_api"]["secret"], "fixture-test")
        self.assertEqual(bundle["manifest"]["listeners"]["proxy"], "127.0.0.1:18893")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_managed_fields_unknown_fields_duplicate_keys_and_conflicts_are_rejected(self):
        for target, text in (("mihomo", "rules: []"), ("sing-box", '{"outbounds":[]}'), ("sing-box", '{"route":{"rules":[]}}'), ("mihomo", "unsupported-root: 123"), ("mihomo", "mixed-port: 18890\nmixed-port: 18891"), ("sing-box", '{"dns":{},"dns":{}}')):
            with self.subTest(target=target, text=text), self.assertRaises(ConfigError):
                parse_base(text, target)
        for text in ("mixed-port: 0", "mixed-port: 1053", "mixed-port: false", "mixed-port: 18891\nsocks-port: 18891", "external-controller: 127.0.0.1:1053", "dns:\n  listen: 127.0.0.1:1053"):
            value = deepcopy(self.value); value["base_configs"] = {"mihomo": text}
            with self.assertRaises(ConfigError):
                compile_bundle(value)
        value = deepcopy(self.value);value["base_configs"] = {"sing-box": '{"inbounds":[]}'}
        with self.assertRaisesRegex(ConfigError, "mixed-in"):
            compile_bundle(value, "sing-box")

    def test_persistence_reset_and_conflict_are_atomic_and_keep_source_comments(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            state = workspace.save_profile({"schema_version": 1, "sources": [], "nodes": [], "groups": [], "library": [], "policies": [], "fallback": "DIRECT", "settings": {}}, 0)
            text = "# my custom base\nlog-level: warning\n"
            saved = workspace.save_base_config("mihomo", text, state["revision"])
            self.assertEqual(Workspace(temporary).snapshot()["profile"]["base_configs"]["mihomo"], text)
            self.assertEqual(saved["profile"]["policies"], state["profile"]["policies"])
            with self.assertRaises(Conflict):
                workspace.save_base_config("mihomo", "", state["revision"])
            with self.assertRaises(ConfigError):
                workspace.save_base_config("mihomo", "rules: []", saved["revision"])
            self.assertEqual(workspace.snapshot(), saved)
            reset = workspace.save_base_config("mihomo", "", saved["revision"])
            self.assertFalse(compile_bundle(reset["profile"])["manifest"]["base_config"]["custom"])

    def test_singbox_base_and_bound_inbounds_merge_with_plan_scope(self):
        value = deepcopy(self.value)
        value.update(active_plan="default", routing_plans=[{"id": "default", "name": "default", "policies": deepcopy(value["policies"]), "fallback": value["fallback"]}])
        value["inbounds"] = [{"id": "home", "name": "home", "protocol": "mixed", "listen": "127.0.0.1", "port": 18894, "plan": "default", "enabled": True}]
        value["base_configs"] = {"sing-box": '{"inbounds":[{"type":"mixed","tag":"mixed-in","listen":"127.0.0.1","listen_port":18893}]}'}
        bundle = compile_bundle(value, "sing-box")
        self.assertEqual({i["tag"] for i in bundle["files"]["sing-box.json"]["inbounds"]}, {"mixed-in", "in_home"})
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
