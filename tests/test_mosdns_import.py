"""Migration integrity and the HTTP draft -> save -> native export boundary."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.mosdns import checked
from yomesh.mosdns_import import import_localdns
from yomesh.native import find_core, validate_bundle
from yomesh.rule_sources import parse_rules
from yomesh.server import handler_for
from test_native_configuration import fixture


def localdns_profile():
    native = fixture()
    docs = native["documents"]
    def doc(name, value):
        docs["localdns/config/" + name + ".json"] = json.dumps(value)
    doc("settings/upstream_overrides", {
        "domestic": [{"addr": "223.5.5.5", "tag": "国内", "enabled": True}, {"addr": "tcp://223.5.5.5", "enabled": False}],
        "foreign": [{"addr": "https://dns.google/dns-query", "dial_addr": "8.8.8.8", "socks5": "192.0.2.7:9666", "enabled": True}],
        "foreign_ecs": [{"addr": "tcp://1.1.1.1", "enabled": True}],
        "foreign_fakeip": [{"addr": "192.0.2.7:1053", "enabled": True}]})
    doc("settings/overrides", {"ecs": "203.0.113.0/24"})
    doc("settings/switches", {"block_aaaa": False, "block_special_qtypes": True, "client_policy": "listed_proxy_only"})
    doc("cache", {"main": {"size": 500000, "lazy_cache_ttl": 604800, "dump_interval": 1800}, "domestic": {"size": 500000}})
    docs["localdns/rules/manual/rewrite_rules.txt"] = "printer.example 192.0.2.10\n"
    docs["localdns/rules/manual/client_ips.txt"] = "192.0.2.88\n"
    docs["localdns/rules/manual/pcdn_ipv4_rules.txt"] = r"^192\.0\.2\."
    return {"schema_version": 1, "fallback": "DIRECT", "native_configuration": native}


class MosDNSImportTests(unittest.TestCase):
    def test_import_preserves_source_and_maps_supported_settings_without_enabling_guessed_routes(self):
        profile = localdns_profile()
        before = deepcopy(profile)
        with patch("yomesh.rule_sources.fetch") as fetch:
            result = import_localdns(profile, ROOT)
            fetch.assert_not_called()
        config = result["settings"]
        self.assertEqual(profile, before)
        self.assertEqual(config["port"], 19553)
        self.assertEqual(config["api"], "127.0.0.1:19099")
        self.assertFalse(config["groups"]["local"]["upstreams"][1]["enabled"])
        self.assertEqual(config["groups"]["remote"]["upstreams"][0]["socks5"], "192.0.2.7:9666")
        self.assertEqual(config["groups"]["ecs"]["ecs"], "203.0.113.0/24")
        self.assertEqual(config["cache"]["size"], 500000)
        self.assertFalse(config["cache"]["enabled"])
        self.assertEqual(config["hosts"], ["printer.example 192.0.2.10"])
        self.assertTrue(config["rules"])
        self.assertTrue(all(not rule["enabled"] for rule in config["rules"]))
        self.assertTrue(any(row["name"] == "PCDN IPv4 正则" and row["status"] == "unsupported" for row in result["report"]["items"]))
        self.assertFalse(config["migration"]["reviewed"])
        with self.assertRaisesRegex(ConfigError, "迁移报告"):
            compile_bundle({**profile, "mosdns": config}, component="dns")
        config["migration"]["reviewed"] = True
        bundle = compile_bundle({**profile, "mosdns": config}, component="dns")
        self.assertTrue(bundle["manifest"]["diagnostics"])
        self.assertEqual(bundle["files"]["migration-report.json"], config["migration"])
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_srs_domain_family_or_is_lossless_but_extra_conditions_reject_whole_source(self):
        value = {"version": 3, "rules": [{"domain": "exact.example", "domain_suffix": ["suffix.example"], "domain_keyword": ["search"], "domain_regex": [r"^ad[0-9]+\.example$"]}]}
        expected = parse_rules(json.dumps(value).encode(), "sing-box", ROOT)[0]["entries"]
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder) / "source.json", Path(folder) / "source.srs"
            source.write_text(json.dumps(value), encoding="utf-8")
            subprocess.run([str(find_core(ROOT, "sing-box")), "rule-set", "compile", str(source), "-o", str(target)], capture_output=True, check=True)
            body = target.read_bytes()
        self.assertEqual(set(parse_rules(body, "srs", ROOT)[0]["entries"]), set(expected))
        self.assertEqual(len(expected), 4)
        for extra in ({"ip_cidr": ["192.0.2.0/24"]}, {"invert": True}, {"port": [443]}):
            candidate = deepcopy(value)
            candidate["rules"][0].update(extra)
            with self.assertRaises(ConfigError):
                parse_rules(json.dumps(candidate).encode(), "sing-box", ROOT)
        profile = localdns_profile()
        native = profile["native_configuration"]
        native["assets"]["localdns/rules/online/custom_cn/test.srs"] = base64.b64encode(body).decode()
        native["documents"]["localdns/config/subscriptions/custom_cn.json"] = json.dumps([{
            "name": "测试来源", "files": "rules/online/custom_cn/test.srs", "detected_format": "srs", "enabled": True,
            "url": "https://example.com/source.srs", "auto_update": True, "update_interval_hours": 12}])
        config = import_localdns(profile, ROOT)["settings"]
        imported = next(row for row in config["rules"] if row["id"] == "ld_custom_cn_0")
        self.assertEqual(len(imported["domains"]), 4)
        self.assertFalse(imported["source"]["auto_update"])
        self.assertEqual(imported["source"]["interval_minutes"], 720)
        self.assertFalse(imported["enabled"])

    def test_unsupported_missing_and_escaping_assets_are_reported_without_partial_matchers(self):
        profile = localdns_profile()
        native = profile["native_configuration"]
        native["documents"]["localdns/config/subscriptions/custom_cn.json"] = json.dumps([
            {"name": "缺失快照", "files": "rules/online/missing.srs", "url": "https://example.com/missing"},
            {"name": "越界路径", "files": "../credentials.json", "url": "https://example.com/source"},
            {"name": "复杂条件", "files": "rules/online/and.json", "detected_format": "sing-box", "url": "https://example.com/source"}])
        native["documents"]["localdns/rules/online/and.json"] = json.dumps({"version": 3, "rules": [{"domain_suffix": ["example.com"], "ip_cidr": ["192.0.2.0/24"]}]})
        result = import_localdns(profile, ROOT)
        self.assertEqual(sum(i["status"] == "unsupported" and i["name"] in {"缺失快照", "越界路径", "复杂条件"} for i in result["report"]["items"]), 3)
        self.assertFalse(any(r["id"].startswith("ld_custom_cn_") for r in result["settings"]["rules"]))
        self.assertNotIn("credentials", json.dumps(result["report"]))
        with self.assertRaises(ConfigError):
            import_localdns({"schema_version": 1}, ROOT)
        result["settings"]["migration"]["reviewed"] = "yes"
        with self.assertRaises(ConfigError):
            checked(result["settings"])

    def test_http_import_is_a_draft_revision_safe_and_save_is_independent_of_proxy(self):
        with tempfile.TemporaryDirectory() as folder:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            server.RequestHandlerClass = handler_for(Path(folder), server.server_port)
            server.RequestHandlerClass.space_catalog.runtime("default").workspace.save_profile(localdns_profile(), 0)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                with urlopen(Request(f"http://127.0.0.1:{server.server_port}" + path, data=data, headers={"Content-Type": "application/json"}), timeout=30) as response:
                    return json.load(response)
            try:
                before = request('/api/workspace')
                draft = request('/api/workspace/mosdns/import-localdns', {"revision": before["revision"]})
                self.assertEqual(request('/api/workspace'), before)
                with self.assertRaises(HTTPError) as error:
                    request('/api/workspace/mosdns/import-localdns', {"revision": -1})
                self.assertEqual(error.exception.code, 409)
                with self.assertRaises(HTTPError):
                    request('/api/workspace/mosdns/import', {"revision": before["revision"], "config": {"unknown": True}})
                self.assertEqual(request('/api/workspace'), before)
                normalized = request('/api/workspace/mosdns/import', {"revision": before["revision"], "config": draft["settings"]})
                saved = request('/api/workspace/mosdns/save', {"revision": before["revision"], "config": normalized["settings"]})
                self.assertEqual(saved["profile"]["native_configuration"], before["profile"]["native_configuration"])
                self.assertEqual(request('/api/workspace/mosdns')["config"], saved["profile"]["mosdns"])
                with self.assertRaises(HTTPError):
                    request('/api/workspace/mosdns/check', {"revision": saved["revision"], "config": saved["profile"]["mosdns"]})
            finally:
                server.shutdown()
                server.RequestHandlerClass.space_catalog.close()
                server.server_close()
