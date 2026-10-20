"""Lossless form saves and truthful export readiness, with no running services."""
import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.model import ConfigError
from yomesh.native_listeners import change_listener
from yomesh.server import handler_for


class NativeListenerTests(unittest.TestCase):
    def listener(self, kind):
        row = {"name": "home", "type": kind, "listen": "0.0.0.0", "port": "19000", "routing-mark": 0, "unfamiliar-option": {"keep": [1, 2]}}
        if kind == "shadowsocks":
            row.update(cipher="aes-128-gcm", password="fixture-secret", udp=True)
        elif kind in {"trojan", "hysteria2"}:
            row.update(certificate="certs/test.crt", **{"private-key": "certs/test.key"}, alpn=[])
            row["users"] = {"owner": "fixture-secret"} if kind == "hysteria2" else [{"username": "owner", "password": "fixture-secret", "extra": "preserve"}]
        return row

    def test_unchanged_forms_preserve_every_native_field_and_type(self):
        for kind in ("mixed", "shadowsocks", "trojan", "hysteria2"):
            with self.subTest(kind=kind):
                n = fixture()
                n["mihomo"]["listeners"] = [self.listener(kind)]
                original = deepcopy(n)
                self.assertEqual(change_listener(n, {"name": "home", "changes": {}, "advanced": {"unfamiliar-option": {"keep": [1, 2]}}}), original)
                self.assertEqual(n, original)

    def test_user_and_port_edits_do_not_rebuild_other_fields(self):
        n = fixture()
        n["mihomo"]["listeners"] = [self.listener("hysteria2")]
        edited = change_listener(n, {"name": "home", "changes": {"port": 19100, "users": {"owner": "updated"}}})
        before = deepcopy(n["mihomo"]["listeners"][0])
        before.update(port=19100, users={"owner": "updated"})
        self.assertEqual(edited["mihomo"]["listeners"][0], before)
        self.assertEqual(n["mihomo"]["listeners"][0]["port"], "19000")

    def test_invalid_or_duplicate_edits_are_atomic(self):
        n = fixture()
        n["mihomo"]["listeners"] = [self.listener("shadowsocks")]
        original = deepcopy(n)
        requests = [
            {"name": "home", "changes": {"name": "renamed"}},
            {"name": "home", "changes": {"type": "mixed"}},
            {"name": "home", "changes": {"port": 70000}},
            {"name": "home", "advanced": {"password": "duplicate"}},
            {"name": "home", "changes": {"password": None}},
            {"changes": {"name": "home", "type": "mixed", "port": 19101}},
        ]
        for request in requests:
            with self.subTest(request=list(request)):
                with self.assertRaises(ConfigError):
                    change_listener(n, request)
                self.assertEqual(n, original)

    def test_export_check_and_listener_revision_use_same_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            handler = handler_for(Path(temporary), 0)
            class Quiet(handler):
                def log_message(self, *args):
                    pass
            server = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
            # handler_for needs the actual origin to apply the same host guard.
            actual = handler_for(Path(temporary), server.server_port)
            class Actual(actual):
                def log_message(self, *args):
                    pass
            server.RequestHandlerClass = Actual
            threading.Thread(target=server.serve_forever, daemon=True).start()
            catalog = actual.space_catalog
            def request(path, body, scope):
                req = Request(f"http://127.0.0.1:{server.server_port}" + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", "X-YoMesh-Space": scope})
                try:
                    with urlopen(req, timeout=10) as response:
                        return response.status, json.load(response)
                except HTTPError as error:
                    return error.code, json.load(error)
            try:
                n = fixture()
                n["mihomo"]["listeners"] = [self.listener("shadowsocks")]
                state = catalog.import_native(n, "native", catalog.state["revision"])
                scope = state["id"]
                before = catalog.workspace_snapshot(scope)
                with patch("yomesh.server.validate_bundle") as validate:
                    code, result = request("/api/workspace/export-check", {"use_workspace": True, "revision": before["revision"], "proxy": "mihomo", "component": "proxy", "rule_output": "remote"}, scope)
                    self.assertEqual(code, 200)
                    self.assertTrue(result["ready"])
                    self.assertEqual(result["native_validation"], "pending")
                    self.assertNotIn("fixture-secret", json.dumps(result))
                    validate.assert_not_called()
                self.assertEqual(catalog.workspace_snapshot(scope), before)
                broken = deepcopy(before["profile"])
                broken["native_configuration"]["policies"][0]["text"] = "RULE-SET,missing,DIRECT"
                _, blocked = request("/api/workspace/export-check", {"profile": broken, "proxy": "mihomo", "component": "proxy", "rule_output": "remote"}, scope)
                self.assertFalse(blocked["ready"])
                self.assertIn("missing", blocked["error"])
                self.assertEqual(catalog.workspace_snapshot(scope), before)
                _, unsupported = request("/api/workspace/export-check", {"use_workspace": True, "revision": before["revision"], "proxy": "sing-box", "component": "proxy", "rule_output": "remote"}, scope)
                self.assertFalse(unsupported["ready"])
                body = {"name": "home", "changes": {"port": 19100}, "revision": before["revision"]}
                code, saved = request("/api/workspace/native-listener", body, scope)
                self.assertEqual(code, 200)
                self.assertEqual(saved["profile"]["native_configuration"]["mihomo"]["listeners"][0]["port"], 19100)
                self.assertEqual(request("/api/workspace/native-listener", body, scope)[0], 409)
            finally:
                catalog.close()
                handler.space_catalog.close()
                server.shutdown()
                server.server_close()
