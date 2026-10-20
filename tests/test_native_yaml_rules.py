"""Mihomo's rules alias is native input, not an implicit source conversion."""
import base64
import io
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle
from yomesh.native_configuration import checked, refresh_provider, resolve, rule_yaml_entries
from yomesh.native_policy import prepare_request

SOURCE = "rules:\n  - IP-CIDR,192.0.2.0/24,BackHome,no-resolve\n  - IP-CIDR6,2001:db8::/32,BackHome,no-resolve\n  - DOMAIN-SUFFIX,example.test,BackHome\n"


def native():
    value = fixture()
    value["mihomo"]["rule-providers"]["one"] = {"type": "http", "behavior": "classical", "format": "yaml", "url": "https://rules.example.test/home.yaml", "path": "rules/one.yaml"}
    value["documents"]["rules/one.yaml"] = SOURCE
    return value


class NativeYamlRuleTests(unittest.TestCase):
    def test_opaque_rules_alias_exports_original_bytes_without_bypassing_checks(self):
        value = native()
        value["documents"].pop("rules/one.yaml")
        value["assets"]["rules/one.yaml"] = base64.b64encode(SOURCE.encode()).decode()
        bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": value}, "mihomo", rule_output="remote", component="proxy")
        self.assertEqual(bundle["files"]["rules/one.yaml"], SOURCE.encode())
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_rules_alias_preserves_original_source_fields_and_exports_natively(self):
        value = native()
        original = deepcopy(value)
        profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": value}
        bundle = compile_bundle(profile, "mihomo", rule_output="remote", component="proxy")
        self.assertEqual(value, original)
        self.assertEqual(checked(value), original)
        self.assertEqual(bundle["files"]["rules/one.yaml"], SOURCE)
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_rules_alias_dependencies_cannot_bypass_missing_or_disabled_rule_checks(self):
        value = native()
        value["documents"]["rules/one.yaml"] = "rules:\n  - AND,((NETWORK,udp),(RULE-SET,missing))\n"
        with self.assertRaisesRegex(ConfigError, "不存在的规则订阅"):
            resolve(value)
        value["mihomo"]["rule-providers"]["missing"] = {"type": "inline", "behavior": "domain", "payload": ["example.test"]}
        value["selection"] = {"rules": ["one"]}
        self.assertEqual(resolve(value)[1]["rules"], ["MATCH,DIRECT"])
        self.assertIn("RULE-SET,missing", value["documents"]["rules/one.yaml"])

    def test_malformed_and_ambiguous_rule_documents_are_rejected(self):
        for body in ("rules: [null]", "rules: [123]", "rules: nope", "unrelated: []", "[]", "payload: []\nrules: []"):
            with self.subTest(body=body), self.assertRaisesRegex(ConfigError, "payload 或 rules"):
                rule_yaml_entries(body)

    def test_http_refresh_keeps_rules_source_and_rejects_bad_refresh_atomically(self):
        value = native()
        original = deepcopy(value)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(SOURCE.encode())):
            refreshed = refresh_provider(value, "rule-providers", "one")
        self.assertEqual(refreshed, original)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(b"rules: [null]")):
            with self.assertRaises(ConfigError):
                refresh_provider(value, "rule-providers", "one")
        self.assertEqual(value, original)

    def test_new_rules_source_auto_detects_classical_without_rewriting(self):
        request = {"draft": {"name": "native-alias", "mode": "rules", "rules": [], "target": "DIRECT"}, "sources": [{"url": "https://rules.example.test/home.yaml"}]}
        with patch("urllib.request.urlopen", return_value=io.BytesIO(SOURCE.encode())):
            updated, row = prepare_request(fixture(), request, fetch=True)
        provider = updated["mihomo"]["rule-providers"]["native-alias"]
        self.assertEqual(provider["behavior"], "classical")
        self.assertEqual(updated["documents"][provider["path"]], SOURCE)
        self.assertNotIn("rule_source_adapters", updated)


if __name__ == "__main__":
    unittest.main()
