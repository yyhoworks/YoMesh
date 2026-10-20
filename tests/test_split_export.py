"""Independent proxy/DNS packages retain semantics and validate only their core."""
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError, Profile
from yomesh.native import find_core, validate_bundle
from yomesh.server import handler_for
from yomesh.workspace import empty_profile
from test_native_configuration import fixture


class SplitExportTests(unittest.TestCase):
    def setUp(self):
        self.value = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))

    def test_proxy_packages_load_without_dns_binary_and_keep_same_proxy_output(self):
        for proxy in ("mihomo", "sing-box"):
            with self.subTest(proxy=proxy):
                combined = compile_bundle(self.value, proxy, rule_output="local")
                bundle = compile_bundle(self.value, proxy, rule_output="local", component="proxy")
                name = proxy + (".yaml" if proxy == "mihomo" else ".json")
                self.assertEqual(bundle["files"][name], combined["files"][name])
                self.assertNotIn("mosdns.yaml", bundle["files"])
                self.assertFalse(any("mosdns_" in path for path in bundle["files"]))
                def core(root, name):
                    self.assertEqual(name, proxy)
                    return find_core(ROOT, name)
                with patch("yomesh.native.find_core", side_effect=core):
                    result = validate_bundle(bundle, ROOT)
                self.assertEqual(set(result), {"status", proxy})

    def test_dns_export_works_with_empty_proxy_draft_and_only_mosdns_binary(self):
        value = empty_profile()
        value["base_configs"] = {"sing-box": "not valid JSON"}
        value["settings"] = {"dns_socks5": "192.0.2.2:7890"}
        bundle = compile_bundle(value, "sing-box", component="dns")
        upstream = next(p for p in bundle["files"]["mosdns.yaml"]["plugins"] if p["tag"] == "remote_dns")
        self.assertEqual(upstream["args"]["socks5"], "192.0.2.2:7890")
        def core(root, name):
            self.assertEqual(name, "mosdns")
            return find_core(ROOT, name)
        with patch("yomesh.native.find_core", side_effect=core):
            self.assertEqual(set(validate_bundle(bundle, ROOT)), {"status", "mosdns"})

    def test_proxy_export_does_not_require_mosdns_socks_inbound(self):
        self.value["base_configs"] = {"sing-box": '{"inbounds":[]}'}
        bundle = compile_bundle(self.value, "sing-box", component="proxy")
        self.assertEqual(bundle["files"]["sing-box.json"]["inbounds"], [])
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        with self.assertRaisesRegex(ConfigError, "mixed-in"):
            compile_bundle(self.value, "sing-box")

    def test_dns_rules_follow_selection_without_proxy_group_requirements(self):
        self.value["groups"][0]["mode"] = "fallback"
        self.value["composition"] = {"groups": [], "rules": [], "policies": None}
        bundle = compile_bundle(self.value, "sing-box", component="dns")
        self.assertNotIn("sing-box.json", bundle["files"])
        self.assertTrue(bundle["manifest"]["composition"]["excluded_policies"])
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        self.value.pop("composition")
        self.value["settings"] = {"dns_follow_rules": False}
        bundle = compile_bundle(self.value, component="dns")
        self.assertFalse(any(p["type"] == "domain_set" for p in bundle["files"]["mosdns.yaml"]["plugins"]))

    def test_dns_keeps_whole_logical_conditions_and_local_snapshots(self):
        for mode in ("inline", "local", "remote"):
            combined = compile_bundle(self.value, rule_output=mode)
            dns = compile_bundle(self.value, rule_output=mode, component="dns")
            self.assertEqual(dns["files"]["mosdns.yaml"], combined["files"]["mosdns.yaml"])
            self.assertEqual(validate_bundle(dns, ROOT)["status"], "passed")

    def test_native_split_preserves_assets_and_dns_ignores_missing_proxy_references(self):
        native = fixture()
        original = deepcopy(native)
        profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": native}
        proxy = compile_bundle(profile, rule_output="remote", component="proxy")
        self.assertFalse(any(p.startswith("localdns/") for p in proxy["files"]))
        self.assertEqual(validate_bundle(proxy, ROOT)["status"], "passed")
        native["policies"][0]["text"] = "RULE-SET,missing,DIRECT"
        dns = compile_bundle(profile, component="dns")
        self.assertNotIn("mihomo.yaml", dns["files"])
        self.assertEqual(dns["files"]["localdns/rules/fixture.bin"], b"\x00\xffnative")
        self.assertEqual(set(dns["manifest"]["versions"]), {"localdns"})
        with self.assertRaises(ConfigError):
            compile_bundle(profile, rule_output="remote", component="proxy")
        native["policies"][0] = original["policies"][0]
        self.assertEqual(native, original)

    def test_invalid_scope_and_socks_endpoints_are_rejected(self):
        for scope in ("unknown", "", None):
            with self.assertRaises(ConfigError):
                compile_bundle(self.value, component=scope)
        for socks in ("http://127.0.0.1:1080", "127.0.0.1:0", "[::1]:70000", "user:pass@127.0.0.1:1080", "127.0.0.1:1080/path"):
            with self.assertRaises(ConfigError):
                Profile({"schema_version": 1, "fallback": "DIRECT", "settings": {"dns_socks5": socks}})

    def test_http_preview_and_zip_export_agree_on_independent_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            handler = handler_for(Path(directory), server.server_port)
            class Quiet(handler):
                def log_message(self, *args):
                    pass
            server.RequestHandlerClass = Quiet
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                for component, core_name in (("proxy", "mihomo"), ("dns", "mosdns")):
                    body = json.dumps({"profile": self.value, "component": component, "rule_output": "local"}).encode()
                    def request(path):
                        return urlopen(Request(f"http://127.0.0.1:{server.server_port}" + path, data=body, headers={"Content-Type": "application/json"}), timeout=30)
                    with request("/api/workspace/preview-export") as response:
                        preview = json.load(response)
                    with patch("yomesh.native.find_core", side_effect=lambda root, name: find_core(ROOT, name)):
                        with request("/api/export") as response:
                            self.assertEqual(response.headers.get_filename(), "yomesh-" + core_name + ".zip")
                            archive = zipfile.ZipFile(io.BytesIO(response.read()))
                    manifest = json.loads(archive.read("manifest.json"))
                    self.assertEqual(set(archive.namelist()), {*preview["files"], "manifest.json"})
                    self.assertEqual(set(manifest["validation"]), {"status", core_name})
                    self.assertEqual(manifest["validation"]["status"], "passed")
            finally:
                handler.space_catalog.close()
                server.shutdown()
                server.server_close()
