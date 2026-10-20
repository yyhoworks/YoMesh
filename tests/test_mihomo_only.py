"""A native client does not acquire unrelated DNS files or lose original bytes."""
import base64
from copy import deepcopy
from unittest import TestCase

from test_native_configuration import fixture, ROOT
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle
from yomesh.native_configuration import checked, overview, summary


class MihomoOnlyTests(TestCase):
    def native_client(self):
        native = fixture()
        native.update(format="mihomo-v1", documents={}, assets={})
        native["mihomo"]["dns"] = {"enable": True, "listen": "127.0.0.1:1053", "nameserver": ["223.5.5.5"]}
        return native

    def test_native_client_preserves_builtin_dns_without_separate_dns(self):
        native = self.native_client()
        before = deepcopy(native)
        self.assertEqual(checked(native), before)
        self.assertIsNone(overview(native)["dns_engine"])
        self.assertIsNone(summary(native)["dns_engine"])
        bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": native}, rule_output="remote")
        self.assertEqual(bundle["manifest"]["component"], "proxy")
        self.assertNotIn("dns_engine", bundle["manifest"])
        self.assertFalse(any(path.startswith("localdns/") or path == "mosdns.yaml" for path in bundle["files"]))
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        self.assertEqual(native, before)

    def test_separate_dns_export_is_explicitly_unavailable(self):
        native = self.native_client()
        with self.assertRaisesRegex(ConfigError, "没有独立 DNS"):
            compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": native}, rule_output="remote", component="dns")

    def test_legacy_localdns_format_still_requires_its_actual_config(self):
        native = self.native_client()
        native["format"] = "mihomo-localdns-v1"
        with self.assertRaisesRegex(ConfigError, "缺少 LocalDNS"):
            checked(native)

    def test_opaque_invalid_rules_file_is_preserved_and_cannot_silently_export_empty(self):
        native = self.native_client()
        raw = b"rules:\n- null\n"
        native["mihomo"]["rule-providers"]["original"] = {"type": "file", "path": "rules/original.yaml", "behavior": "classical", "format": "yaml"}
        native["assets"]["rules/original.yaml"] = base64.b64encode(raw).decode()
        native["policies"][0]["text"] = "RULE-SET,original,DIRECT"
        self.assertEqual(base64.b64decode(checked(native)["assets"]["rules/original.yaml"]), raw)
        self.assertFalse(summary(native)["valid"])
        with self.assertRaisesRegex(ConfigError, "payload"):
            compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": native}, rule_output="remote", component="proxy")
