"""Explicit source adaptation keeps every predicate and passes the real core."""
import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle
from yomesh.native_configuration import refresh_provider
from yomesh.native_policy import change
from yomesh.native_rule_groups import rule_groups
from yomesh.native_rule_sources import normalize_rule_list

SOURCE = "rules:\n- IP-CIDR,198.51.100.0/24,HomeLAN,no-resolve\n- IP-CIDR6,fc00::/7,HomeLAN,no-resolve\n- DOMAIN-SUFFIX,lan,HomeLAN\n"


class NativeRuleSourceTests(unittest.TestCase):
    def test_normalization_retains_predicates_suffixes_and_source_label(self):
        body, label = normalize_rule_list(SOURCE)
        self.assertEqual(label, "HomeLAN")
        self.assertEqual(yaml.safe_load(body)["payload"], ["IP-CIDR,198.51.100.0/24,no-resolve", "IP-CIDR6,fc00::/7,no-resolve", "DOMAIN-SUFFIX,lan"])
        for invalid in (SOURCE + "other: true\n", SOURCE.replace("lan,HomeLAN", "lan,DIRECT"), "rules:\n- DST-PORT,70000,DIRECT\n", "rules:\n- MATCH,DIRECT\n", "rules:\n- OR,((NETWORK,udp),(DST-PORT,443)),DIRECT\n"):
            with self.assertRaises(ConfigError):
                normalize_rule_list(invalid)

    def test_ui_request_fetches_preserves_source_and_exports_valid_file_provider(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(SOURCE.encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        n = fixture()
        before = deepcopy(n)
        url = f"http://127.0.0.1:{server.server_port}/home.yaml"
        request = {"action": "unit-save", "draft": {"name": "HomeLAN", "mode": "rules", "rules": [], "target": "直连组"}, "sources": [{"url": url, "source_format": "clash-rules"}], "outlet": {"type": "select", "members": ["DIRECT"], "default": "DIRECT"}}
        try:
            result = change(n, request)
            adapter = result["rule_source_adapters"]["HomeLAN"]
            self.assertEqual(result["documents"][adapter["source_path"]], SOURCE)
            self.assertEqual(result["mihomo"]["rule-providers"]["HomeLAN"]["url"], url)
            self.assertEqual(result["mihomo"]["rule-providers"]["HomeLAN"]["type"], "http")
            view = next(u for u in rule_groups(result) if u["name"] == "HomeLAN")
            self.assertEqual(view["source_settings"][0]["source_format"], "clash-rules")
            bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": result}, "mihomo", rule_output="remote", component="proxy")
            provider = yaml.safe_load(bundle["files"]["mihomo.yaml"])["rule-providers"]["HomeLAN"]
            self.assertEqual(provider["type"], "file")
            self.assertEqual(provider["url"], url)
            self.assertEqual(bundle["manifest"]["rule_output"]["adapters"][0]["source_target"], "HomeLAN")
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
            self.assertEqual(refresh_provider(result, "rule-providers", "HomeLAN"), result)
            self.assertEqual(n, before)
        finally:
            server.shutdown()
            server.server_close()

    def test_matcher_controls_recreate_ports_domain_and_ip_without_advanced_text(self):
        for kind, value, target, expected in [("DST-PORT", "19302", "REJECT", "DST-PORT,19302,REJECT"), ("DST-PORT", "5349", "REJECT", "DST-PORT,5349,REJECT"), ("DST-PORT", "3478", "REJECT", "DST-PORT,3478,REJECT"), ("DOMAIN", "router.example", "DIRECT", "DOMAIN,router.example,DIRECT"), ("IP-CIDR", "192.0.2.0/24", "DIRECT", "IP-CIDR,192.0.2.0/24,DIRECT")]:
            result = change(fixture(), {"action": "unit-save", "draft": {"name": "simple", "mode": "matcher", "matcher_type": kind, "values": [value], "target": target}})
            self.assertEqual(result["policies"][-2]["text"], expected)
