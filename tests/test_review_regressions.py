"""Regression coverage for fail-closed auth, atomic saves and isolated listeners."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.auth import AuthStore, AuthUnavailable
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import validate_bundle
from yomesh.server import handler_for
from yomesh.spaces import ConfigurationSpaces
from yomesh.workspace import Workspace


class AuthenticationRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = AuthStore(self.root)
        self.store.update_credentials("", "管理员", "fixture-password")

    def tearDown(self):
        self.temp.cleanup()

    def test_unicode_username_round_trip_and_wrong_login(self):
        token = self.store.login(" 管理员 ", "fixture-password")
        self.assertEqual(self.store.verify_cookie("yomesh_session=" + token), "管理员")
        with self.assertRaises(ConfigError):
            self.store.login("其他人", "fixture-password")

    def test_unreadable_missing_and_malformed_credentials_fail_closed(self):
        for failure in (PermissionError(), FileNotFoundError()):
            with self.subTest(failure=type(failure).__name__), patch.object(Path, "read_text", side_effect=failure):
                with self.assertRaises(AuthUnavailable):
                    self.store.is_enabled()
        for raw in ("{", "[]", "{}", '{"enabled":false}', '{"enabled":true,"username":"admin"}'):
            with self.subTest(raw=raw), patch.object(Path, "read_text", return_value=raw):
                with self.assertRaises(AuthUnavailable):
                    self.store.is_enabled()
                with self.assertRaises(AuthUnavailable):
                    self.store.update_credentials("", "attacker", "another-password")

    def test_fresh_unconfigured_and_explicitly_disabled_remain_supported(self):
        self.assertFalse(AuthStore(self.root / "fresh").is_enabled())
        self.store.disable_auth("fixture-password")
        self.assertFalse(AuthStore(self.root).is_enabled())


class LoginPageRegressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "web", self.root / "web")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.root, 8767))
        self.handler = self.server.RequestHandlerClass
        self.handler.log_message = lambda *args: None
        self.handler.auth_store.update_credentials("", "管理员", "fixture-password")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.handler.space_catalog.close()
        self.temp.cleanup()

    def request(self, path, body=None, cookie=None, method=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        headers = {"Host": "127.0.0.1:8767", "Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        try:
            conn.request(method or ("POST" if body is not None else "GET"), path,
                         None if body is None else json.dumps(body).encode(), headers)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_html_is_gated_before_render_including_aliases_and_head(self):
        for path in ("/", "/index.html", "/?test=1", "/%69ndex.html", "/./index.html"):
            for method in ("GET", "HEAD"):
                with self.subTest(path=path, method=method):
                    status, headers, body = self.request(path, method=method)
                    self.assertEqual(status, 302)
                    self.assertEqual(headers["Location"], "/login.html")
                    self.assertEqual(body, b"")
        status, _, body = self.request("/login.html")
        self.assertEqual(status, 200)
        self.assertIn(b'id="login-form"', body)
        self.assertNotIn(b'class="sidebar"', body)
        self.assertNotIn(b'workspace.js', body)
        self.assertEqual(self.request("/login.js")[0], 200)
        self.assertEqual(self.request("/api/workspace")[0], 401)

    def test_login_opens_home_and_logout_clears_browser_cookie(self):
        status, headers, _ = self.request("/api/auth/login", {"username": "管理员", "password": "fixture-password"})
        self.assertEqual(status, 200)
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, body = self.request("/", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn(b'class="sidebar"', body)
        self.assertEqual(self.request("/api/workspace", cookie=cookie)[0], 200)
        status, headers, _ = self.request("/api/auth/logout", {}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.request("/")[0], 302)

    def test_storage_failure_blocks_management_but_not_login_assets(self):
        original = Path.read_text
        def unavailable(path, *args, **kwargs):
            if path == self.handler.auth_store.path:
                raise PermissionError()
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", unavailable):
            for path, body in (("/api/auth/status", None), ("/api/workspace", None),
                               ("/api/auth/login", {"username": "admin", "password": "test"}),
                               ("/api/auth/update", {"username": "admin", "new_password": "test"})):
                status, _, raw = self.request(path, body)
                self.assertEqual(status, 503, path)
                self.assertTrue(json.loads(raw)["auth_unavailable"])
            self.assertEqual(self.request("/")[0], 302)
            self.assertEqual(self.request("/login.html")[0], 200)
            self.assertEqual(self.request("/s/" + "a" * 43 + "/mihomo.yaml")[0], 404)


class SaveRegressions(unittest.TestCase):
    def test_workspace_failed_save_restores_entire_state_and_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Workspace(folder)
            workspace._write()
            before = deepcopy(workspace.state)
            on_disk = workspace.file.read_bytes()
            profile = deepcopy(before["profile"])
            profile["name"] = "failed edit"
            with patch("yomesh.workspace.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    workspace.save_profile(profile, before["revision"])
            self.assertEqual(workspace.state, before)
            self.assertEqual(workspace.file.read_bytes(), on_disk)

    def test_ordinary_and_shared_space_failure_rolls_back_both_documents(self):
        for shared in (False, True):
            with self.subTest(shared=shared), tempfile.TemporaryDirectory() as folder:
                spaces = ConfigurationSpaces(Path(folder))
                try:
                    ident = spaces.save({"name": "shared"}, spaces.state["revision"], copy_from="default")["id"] if shared else "default"
                    workspace = spaces.runtime(ident).workspace
                    workspace._write()
                    before, catalog = deepcopy(workspace.state), deepcopy(spaces.state)
                    profile = spaces.workspace_snapshot(ident)["profile"]
                    profile["name"] = "failed edit"
                    profile["settings"]["dns_port"] = 2053
                    # Fail after the workspace write, at the catalog write for
                    # shared overrides; ordinary spaces fail inside workspace.
                    target = spaces if shared else workspace
                    with patch.object(target, "_write", side_effect=OSError("disk full")):
                        with self.assertRaises(OSError):
                            spaces.save_profile(profile, before["revision"], ident)
                    self.assertEqual(workspace.state, before)
                    self.assertEqual(spaces.state, catalog)
                    self.assertEqual(json.loads(workspace.file.read_text(encoding="utf-8")), before)
                    self.assertEqual(json.loads(spaces.file.read_text(encoding="utf-8")), catalog)
                    self.assertFalse((Path(folder) / "data/.resource-transaction.json").exists())
                    self.assertFalse((Path(folder) / "data/spaces" / ident / "workspace.json").exists())
                finally:
                    spaces.close()


class ListenerRegressions(unittest.TestCase):
    def test_isolation_does_not_hide_duplicate_listener_addresses(self):
        plugins = [{"tag": "main", "type": "sequence", "args": [{"exec": "reject 3"}]}]
        plugins.extend({"tag": name, "type": "tcp_server", "args": {"entry": "main", "listen": "127.0.0.1:1053"}} for name in ("one", "two"))
        bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "mosdns": {
            "mode": "custom", "custom_config": {"plugins": plugins}}}, component="dns")
        with self.assertRaisesRegex(ConfigError, "监听地址重复"):
            validate_bundle(bundle, ROOT)

    def test_multiple_same_protocol_listeners_are_isolated_without_rewriting_export(self):
        for protocols in (("tcp", "tcp"), ("udp", "udp"), ("tcp", "udp", "tcp", "udp")):
            with self.subTest(protocols=protocols):
                plugins = [{"tag": "main", "type": "sequence", "args": [{"exec": "reject 3"}]}]
                plugins.extend({"tag": f"listener_{i}", "type": kind + "_server", "args": {"entry": "main", "listen": f"192.0.2.1:{1053+i}"}} for i, kind in enumerate(protocols))
                bundle = compile_bundle({"schema_version": 1, "fallback": "DIRECT", "mosdns": {
                    "mode": "custom", "custom_config": {"plugins": plugins}}}, component="dns")
                before = deepcopy(bundle["files"])
                self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
                self.assertEqual(bundle["files"], before)


if __name__ == "__main__":
    unittest.main()
