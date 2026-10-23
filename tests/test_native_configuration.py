"""Native-space integrity, selection semantics and binary asset packaging."""
import base64
import json
import sys
import tempfile
import unittest
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.model import ConfigError, Profile
from yomesh.native_configuration import checked, resolve, summary, overview
from yomesh.compiler import compile_bundle
from yomesh.native import write_bundle_files, validate_bundle, find_core, run_check
from yomesh.spaces import ConfigurationSpaces
from yomesh.server import handler_for


def fixture():
    return {"format": "mihomo-localdns-v1", "mihomo": {"mixed-port": 17890, "proxies": [], "proxy-providers": {}, "proxy-groups": [{"name": "直连组", "type": "select", "proxies": ["DIRECT"]}], "rule-providers": {"one": {"type": "inline", "behavior": "domain", "payload": ["example.com"]}}, "listeners": []}, "policies": [{"id": "first", "text": "RULE-SET,one,直连组", "enabled": True}, {"id": "final", "text": "MATCH,DIRECT", "enabled": True}], "documents": {"localdns/config/localdns.json": json.dumps({"memory_limit_mb": 150, "api": {"http": "127.0.0.1:19099"}, "dns": {"udp_listen": "127.0.0.1:19553", "tcp_listen": "127.0.0.1:19553"}}), "localdns/rules/manual/proxy_domains.txt": "example.com\n"}, "assets": {"localdns/rules/fixture.bin": base64.b64encode(b"\x00\xffnative").decode()}}


class NativeConfigurationTests(unittest.TestCase):
    def test_node_origin_is_explicit_even_when_provider_is_named_manual(self):
        n = fixture()
        n["mihomo"]["proxy-providers"]["手动"] = {"type": "file", "path": "providers/test.yaml"}
        n["documents"]["providers/test.yaml"] = "proxies:\n- name: subscribed\n  type: ss\n  server: example.test\n  port: 443\n  password: secret\n"
        n["mihomo"]["proxies"] = [{"name": "home", "type": "ss", "server": "home.test", "port": 443, "password": "private"}]
        nodes = {row["name"]: row for row in overview(n)["nodes"]}
        self.assertEqual(nodes["subscribed"]["origin"], "subscription")
        self.assertEqual(nodes["home"]["origin"], "manual")
        self.assertNotIn("password", nodes["home"])
        self.assertNotIn("password", nodes["subscribed"])

    def test_explicit_draft_import_keeps_original_but_cannot_export(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            handler = handler_for(Path(directory), server.server_port)
            class Quiet(handler):
                def log_message(self, *args):pass
            server.RequestHandlerClass = Quiet
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(path, value):
                req = Request(f"http://127.0.0.1:{server.server_port}"+path, data=json.dumps(value).encode(), headers={"Content-Type":"application/json"})
                try:
                    with urlopen(req, timeout=5) as response:return response.status, json.load(response)
                except HTTPError as error:return error.code, json.load(error)
            try:
                n = fixture()
                n["policies"][0]["text"] = "RULE-SET,missing,DIRECT"
                body = {"configuration":n,"name":"原件","revision":handler.space_catalog.snapshot()["revision"]}
                with patch("yomesh.server.validate_bundle") as validate:
                    self.assertEqual(request("/api/spaces/import-native",body)[0],422)
                    code, state = request("/api/spaces/import-native",{**body,"draft":True})
                    self.assertEqual(code,200)
                    validate.assert_not_called()
                imported = handler.space_catalog.workspace_snapshot(state["id"])
                self.assertEqual(imported["profile"]["native_configuration"],n)
                self.assertFalse(next(s for s in state["spaces"] if s["id"]==state["id"])["generation"]["valid"])
                self.assertEqual(request("/api/export",{"profile":imported["profile"],"proxy":"mihomo","rule_output":"remote"})[0],422)
            finally:
                handler.space_catalog.close()
                server.shutdown();server.server_close()

    def test_native_fields_and_binary_assets_are_not_dropped(self):
        n = fixture()
        n["mihomo"]["profile"] = {"store-fake-ip": True, "store-selected": True}
        n["mihomo"]["proxy-groups"][0]["lazy"] = True
        original = deepcopy(n)
        profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": n}
        bundle = compile_bundle(profile, "mihomo", rule_output="remote")
        self.assertEqual(n, original)
        self.assertIn("store-fake-ip", bundle["files"]["mihomo.yaml"])
        with tempfile.TemporaryDirectory() as directory:
            write_bundle_files(Path(directory), bundle["files"])
            self.assertEqual((Path(directory)/"localdns/rules/fixture.bin").read_bytes(), b"\x00\xffnative")
        self.assertEqual(Profile(profile).native_configuration, n)

    def test_disabled_rule_skips_whole_nested_policy(self):
        n = fixture()
        n["policies"][0]["text"] = "AND,((RULE-SET,one),(NETWORK,udp)),直连组"
        n["selection"] = {"rules": []}
        _, native, excluded = resolve(n)
        self.assertEqual(native["rules"], ["MATCH,DIRECT"])
        self.assertEqual(native["rule-providers"], {})
        self.assertEqual(excluded[0]["id"], "first")
        self.assertEqual(n["policies"][0]["text"], "AND,((RULE-SET,one),(NETWORK,udp)),直连组")

    def test_nested_provider_missing_reference_blocks_export(self):
        n = fixture()
        n["mihomo"]["rule-providers"]["one"] = {"type": "inline", "behavior": "classical", "payload": ["AND,((NETWORK,udp),(RULE-SET,missing))"]}
        self.assertFalse(summary(n)["valid"])
        with self.assertRaisesRegex(ConfigError, "不存在的规则订阅"):
            compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": n}, "mihomo", rule_output="remote")

    def test_disabled_broken_provider_is_not_evaluated(self):
        n = fixture()
        n["mihomo"]["rule-providers"]["one"] = {"type": "inline", "behavior": "classical", "payload": ["RULE-SET,missing"]}
        n["selection"] = {"rules": []}
        self.assertEqual(resolve(n)[1]["rules"], ["MATCH,DIRECT"])
        n.pop("selection")
        n["policies"][0]["enabled"] = False
        self.assertTrue(summary(n)["valid"])
        n["policies"][0].update(enabled=True, text="RULE-SET,absent,DIRECT")
        self.assertFalse(summary(n)["valid"])

    def test_paths_json_duplicates_and_wrong_target_are_blocked(self):
        for path in ("../secret", "/etc/passwd", "C:/secret", "a\\b", "manifest.json"):
            n = fixture();n["documents"][path] = "sensitive"
            with self.assertRaises(ConfigError):checked(n)
        n = fixture();n["documents"]["localdns/config/localdns.json"] = '{"dns":{},"dns":{}}'
        with self.assertRaises(ConfigError):checked(n)
        profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": fixture()}
        with self.assertRaises(ConfigError):compile_bundle(profile, "sing-box", rule_output="remote")
        with self.assertRaises(ConfigError):compile_bundle(profile, "mihomo", rule_output="inline")

    def test_malformed_caches_are_rejected_before_saving(self):
        n = fixture()
        n["mihomo"]["proxy-providers"]["source"] = {"type":"file","path":"providers/source.yaml"}
        n["documents"]["providers/source.yaml"] = 'proxies: [null]'
        with self.assertRaisesRegex(ConfigError, "缓存格式无效"):checked(n)
        n["documents"]["providers/source.yaml"] = 'proxies: []'
        n["mihomo"]["rule-providers"]["one"] = {"type":"file","path":"rules/one.yaml","behavior":"classical"}
        n["documents"]["rules/one.yaml"] = 'payload: [null]'
        with self.assertRaisesRegex(ConfigError, "payload"):checked(n)

    def test_import_and_edit_stay_isolated_from_existing_space(self):
        with tempfile.TemporaryDirectory() as directory:
            spaces = ConfigurationSpaces(directory)
            try:
                original = spaces.workspace_snapshot("default")
                imported = spaces.import_native(fixture(), "原生设备", spaces.snapshot()["revision"])
                ident = imported["id"]
                value = spaces.workspace_snapshot(ident)
                value["profile"]["native_configuration"]["mihomo"]["mixed-port"] = 18888
                spaces.save_profile(value["profile"], value["revision"], ident)
                self.assertEqual(spaces.workspace_snapshot("default"), original)
                self.assertEqual(spaces.workspace_snapshot(ident)["profile"]["native_configuration"]["mihomo"]["mixed-port"], 18888)
            finally:spaces.close()

    def test_absolute_external_ui_is_normalized_for_portable_validation(self):
        n = fixture()
        n["format"] = "mihomo-v1"
        n["mihomo"]["external-ui"] = "/etc/mihomo/ui"
        profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": n}
        bundle = compile_bundle(profile, "mihomo", rule_output="remote", component="proxy")
        import yaml
        self.assertEqual(yaml.safe_load(bundle["files"]["mihomo.yaml"])["external-ui"], "ui")
        result = validate_bundle(bundle, ROOT)
        self.assertEqual(result["mihomo"]["status"], "passed")

    @unittest.skipIf(sys.platform == "win32", "LocalDNS FJGAC is a pinned Linux binary")
    def test_native_localdns_check_uses_pinned_binary(self):
        if not (ROOT/"tools/cores/localdns/localdns").exists():
            self.skipTest("LocalDNS native core not installed")
        n = fixture()
        n["documents"].update({"localdns/config/upstreams/domestic.json": '{"upstreams":[]}', "localdns/config/settings/switches.json": '{}', "localdns/config/subscriptions/domestic.json": '[]'})
        for name in ("foreign", "foreign_ecs", "proxy"):
            n["documents"][f"localdns/config/upstreams/{name}.json"] = '{"upstreams":[]}'
        for name in ("block_domains", "client_ips", "ddns_domains", "direct_cidrs", "direct_domains", "pcdn_domains", "pcdn_ipv4_rules", "pcdn_ipv6_rules", "realip_domains", "rewrite_rules"):
            n["documents"][f"localdns/rules/manual/{name}.txt"] = ''
        for name in ("realip_rules", "fakeip_rules", "nodata_rules"):
            n["documents"][f"localdns/generated/{name}.txt"] = ''
        n["documents"]["localdns/rules/compiled/adguard.txt"] = ''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            for name in ("geosite_cn", "geosite_non_cn", "geoip_cn", "custom_cn", "custom_non_cn"):
                source, target = path / "rule.json", path / "rule.srs"
                source.write_text(json.dumps({"version":3,"rules":[{"ip_cidr":["192.0.2.0/24"]} if name=="geoip_cn" else {"domain_suffix":["example.test"]}]}))
                run_check([find_core(ROOT, "sing-box"), "rule-set", "compile", "--output", target, source], path)
                n["assets"][f"localdns/rules/compiled/{name}.srs"] = base64.b64encode(target.read_bytes()).decode()
        for name in ("audit", "client_names", "requery"):
            n["documents"][f"localdns/config/settings/{name}.json"] = '{}'
        n["documents"]["localdns/config/settings/audit.json"] = '{"capacity":100}'
        n["documents"]["localdns/config/cache.json"] = json.dumps({name:{"size":10,"lazy_cache_ttl":60,"dump_interval":0} for name in ("main","domestic","foreign","foreign_ecs")})
        n["documents"]["localdns/config/generated.json"] = json.dumps({name:{"max_entries":10,"dump_interval":0} for name in ("domain_stats","realip","fakeip","nodata")})
        profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": n}
        bundle = compile_bundle(profile, "mihomo", rule_output="remote")
        result = validate_bundle(bundle, ROOT)
        self.assertEqual(result["localdns"]["status"], "passed")
        self.assertNotIn("mosdns", result)
