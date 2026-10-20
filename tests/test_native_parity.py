"""Native cores are authoritative even when a MosDNS migration draft exists."""
import base64
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.dns_engine import engine_for
from yomesh.model import ConfigError
from yomesh.mosdns import defaults
from yomesh.server import handler_for
from test_native_configuration import fixture


class NativeParityTests(unittest.TestCase):
    def profile(self):
        native = fixture()
        native["mihomo"]["dns"] = {"enable": True, "enhanced-mode": "fake-ip", "fake-ip-filter": ["+.home.arpa"]}
        native["mihomo"]["future-field"] = {"opaque": [1, False, "原生值"]}
        native["mihomo"]["proxy-groups"][0]["future-field"] = {"keep": True}
        native["documents"]["localdns/config/settings/switches.json"] = '{ "route_cache": true, "client_policy": "listed_proxy_only", "unknown": [1, 2] }\n'
        native["documents"]["localdns/config/settings/requery.json"] = '{"enabled":true,"interval":1800}\n'
        # A migration draft must neither override native DNS nor block its export.
        mosdns = defaults()
        mosdns["migration"] = {"source": "LocalDNS", "reviewed": False, "items": []}
        return {"schema_version": 1, "fallback": "DIRECT", "native_configuration": native, "mosdns": mosdns}

    def test_localdns_is_default_and_every_file_is_byte_identical(self):
        value = self.profile()
        original = deepcopy(value)
        self.assertEqual(engine_for(value), "localdns")
        for mode in ("remote", "inline", "local"):
            bundle = compile_bundle(value, component="dns", rule_output=mode)
            self.assertEqual(bundle["manifest"]["dns_engine"], "localdns")
            self.assertNotIn("mosdns.yaml", bundle["files"])
            for name, body in value["native_configuration"]["documents"].items():
                if name.startswith("localdns/"):
                    self.assertEqual(bundle["files"][name], body)
            for name, body in value["native_configuration"]["assets"].items():
                if name.startswith("localdns/"):
                    self.assertEqual(bundle["files"][name], base64.b64decode(body))
        self.assertEqual(value, original)

    def test_mihomo_fields_group_type_and_order_are_preserved_in_split_and_combined(self):
        value = self.profile()
        native = value["native_configuration"]
        expected = deepcopy(native["mihomo"])
        expected["rules"] = [r["text"] for r in native["policies"] if r["enabled"]]
        for component in ("proxy", "both"):
            bundle = compile_bundle(value, component=component, rule_output="remote")
            self.assertEqual(yaml.safe_load(bundle["files"]["mihomo.yaml"]), expected)
            self.assertNotIn("mosdns.yaml", bundle["files"])
            if component == "both":
                self.assertEqual(bundle["manifest"]["dns_engine"], "localdns")
        value["native_configuration"]["policies"][0]["enabled"] = False
        proxy = yaml.safe_load(compile_bundle(value, component="proxy", rule_output="remote")["files"]["mihomo.yaml"])
        self.assertEqual(proxy["rules"], ["MATCH,DIRECT"])

    def test_no_silent_fallback_and_explicit_mosdns_remains_separate(self):
        value = self.profile()
        with self.assertRaisesRegex(ConfigError, "迁移报告"):
            compile_bundle(value, component="dns", dns_engine="mosdns")
        with self.assertRaises(ConfigError):
            compile_bundle({"schema_version": 1}, component="dns", dns_engine="localdns")
        with self.assertRaises(ConfigError):
            compile_bundle(value, component="dns", dns_engine="invalid")
        value["native_configuration"]["documents"].pop("localdns/config/localdns.json")
        with self.assertRaisesRegex(ConfigError, "LocalDNS"):
            compile_bundle(value, component="dns")

    def test_http_preview_check_and_zip_choose_same_core_and_retain_saved_data(self):
        with tempfile.TemporaryDirectory() as folder:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            handler = handler_for(Path(folder), server.server_port)
            server.RequestHandlerClass = handler
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                return urlopen(Request(f"http://127.0.0.1:{server.server_port}" + path, data=data, headers={"Content-Type": "application/json"}), timeout=15)
            def fake_validation(bundle, root):
                self.assertEqual(bundle["manifest"]["dns_engine"], "localdns")
                bundle["manifest"]["validation"] = {"status": "passed", "localdns": {"method": "test-double"}}
                return bundle["manifest"]["validation"]
            try:
                catalog = handler.space_catalog
                catalog.runtime("default").workspace.save_profile(self.profile(), 0)
                before = catalog.workspace_snapshot("default")
                self.assertEqual(catalog.snapshot()["spaces"][0]["dns_engine"], "localdns")
                body = {"use_workspace": True, "revision": before["revision"], "component": "dns"}
                with request("/api/workspace/preview-export", body) as response:
                    preview = json.load(response)
                self.assertEqual(preview["manifest"]["dns_engine"], "localdns")
                with patch("yomesh.server.validate_bundle", side_effect=fake_validation):
                    with request("/api/export", body) as response:
                        self.assertEqual(response.headers.get_filename(), "yomesh-localdns.zip")
                        archive = zipfile.ZipFile(io.BytesIO(response.read()))
                    with request("/api/workspace/check", body) as response:
                        self.assertEqual(json.load(response)["validation"]["status"], "passed")
                self.assertEqual(archive.read("localdns/config/settings/switches.json").decode(), self.profile()["native_configuration"]["documents"]["localdns/config/settings/switches.json"])
                # The optional migration editor still checks MosDNS explicitly.
                with request("/api/workspace/mosdns/preview", {"revision": before["revision"], "config": defaults()}) as response:
                    self.assertIn("plugins", json.load(response)["config"])
                with patch("yomesh.server.validate_bundle", side_effect=ConfigError("缺少 LocalDNS 内核")):
                    with self.assertRaises(HTTPError) as error:
                        request("/api/export", body)
                    self.assertEqual(error.exception.code, 422)
                self.assertEqual(catalog.workspace_snapshot("default"), before)
            finally:
                handler.space_catalog.close()
                server.shutdown()
                server.server_close()
