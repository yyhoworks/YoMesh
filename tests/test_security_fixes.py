"""Regressions for the verified authentication, import and setup defects."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yomesh.auth import AuthStore
from yomesh.compiler import compile_bundle
from yomesh.config_import import import_configuration_space, _convert_singbox_to_profile
from yomesh.hermes_setup import installation_command
from yomesh.model import ConfigError
from yomesh.quick_setup import ClientControllers, quick_create_space
from yomesh.server import handler_for
from yomesh.spaces import ConfigurationSpaces
from helpers import fetched_fixture


class AuthenticationFixes(unittest.TestCase):
    def test_bootstrap_requires_explicit_password_and_preserves_existing_account(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"YOMESH_REQUIRE_AUTH": "1", "YOMESH_INIT_PASSWORD": ""}):
            root = Path(temporary)
            # 密码长度不再限制，但必须显式提供非空初始密码。
            with self.assertRaises(ConfigError):
                AuthStore(root)
            self.assertFalse((root / "data/auth.json").exists())
            with patch.dict(os.environ, {"YOMESH_INIT_PASSWORD": "synthetic-valid-password"}):
                store = AuthStore(root)
                self.assertTrue(store.login("admin", "synthetic-valid-password"))
            self.assertTrue(AuthStore(root).is_enabled())
            before = (root / "data/auth.json").read_bytes()
            with self.assertRaises(ConfigError):
                store.update_credentials("synthetic-valid-password", "admin", "")
            self.assertEqual(before, (root / "data/auth.json").read_bytes())

    def test_credential_replace_failure_preserves_old_login_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = AuthStore(Path(temporary))
            store.update_credentials("", "admin", "synthetic-valid-password")
            before = store.path.read_bytes()
            with patch("yomesh.auth.os.replace", side_effect=PermissionError):
                with self.assertRaises(OSError):
                    store.disable_auth("synthetic-valid-password")
            self.assertEqual(before, store.path.read_bytes())
            self.assertTrue(store.login("admin", "synthetic-valid-password"))
            self.assertEqual(list(store.path.parent.glob(".auth-*")), [])

    def test_query_credentials_do_not_appear_in_request_logs(self):
        with tempfile.TemporaryDirectory() as temporary:
            handler = handler_for(Path(temporary), 8767)
            try:
                request = object.__new__(handler)
                request.path = "/mcp?token=ymh_synthetic_log_secret"
                request.client_address = ("127.0.0.1", 12345)
                output = io.StringIO()
                with redirect_stderr(output):
                    request.log_message('"%s" %s %s', "POST " + request.path + " HTTP/1.1", "200", "-")
                self.assertNotIn("ymh_synthetic_log_secret", output.getvalue())
                self.assertIn("/mcp", output.getvalue())
            finally:
                handler.space_catalog.close()


class ControllerFixes(unittest.TestCase):
    def test_controller_response_reads_are_bounded(self):
        with self.assertRaises(ValueError):
            ClientControllers._response_json(io.BytesIO(b" " * 65537))
        with self.assertRaises(ValueError):
            ClientControllers._response_json(io.BytesIO(b"[]"))
        self.assertEqual(ClientControllers._response_json(io.BytesIO(b'{"version":"fixture"}'))["version"], "fixture")

    def test_blank_preserves_secret_and_explicit_clear_removes_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ClientControllers(Path(temporary))
            body = {"url": "http://127.0.0.1:19090", "test": False}
            store.update("s", {**body, "secret": "synthetic-secret"})
            listed = store.listing()["controllers"]["s"]
            self.assertNotIn("secret", listed)
            self.assertTrue(store.update("s", {**body, "secret": ""})["controllers"]["s"]["has_secret"])
            self.assertFalse(store.update("s", {**body, "secret": "", "clear_secret": True})["controllers"]["s"]["has_secret"])

    def test_corrupt_file_and_url_credentials_do_not_overwrite_saved_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = ClientControllers(Path(temporary))
            store.path.parent.mkdir()
            store.path.write_text("{", encoding="utf-8")
            with self.assertRaises(ConfigError):
                store.update("s", {"url": "http://127.0.0.1:19090", "test": False})
            self.assertEqual(store.path.read_text(encoding="utf-8"), "{")
            store.path.unlink()
            with self.assertRaises(ConfigError):
                store.update("s", {"url": "http://user:synthetic@example.test:9090", "test": False})
            self.assertFalse(store.path.exists())


class QuickSetupFixes(unittest.TestCase):
    def test_fetch_failure_and_empty_url_do_not_create_or_publish_a_space(self):
        with tempfile.TemporaryDirectory() as temporary:
            spaces = ConfigurationSpaces(temporary)
            from unittest.mock import Mock
            publisher = Mock()
            try:
                before = spaces.snapshot()
                with self.assertRaises(ConfigError):
                    quick_create_space(spaces, publisher, {}, "http://127.0.0.1:8767")
                with patch("yomesh.quick_setup.fetch_subscription", side_effect=ConfigError("synthetic offline")):
                    with self.assertRaises(ConfigError):
                        quick_create_space(spaces, publisher, {"subscription_url": "https://fixture.example/sub"}, "http://127.0.0.1:8767")
                self.assertEqual(before, spaces.snapshot())
                publisher.create.assert_not_called()
            finally:
                spaces.close()

    def test_only_fetched_nodes_are_published_and_publication_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            spaces = ConfigurationSpaces(temporary)
            from unittest.mock import Mock
            publisher = Mock()
            seen = []
            def publish(ident, request):
                profile = spaces.workspace_snapshot(ident)["profile"]
                seen.extend(profile["nodes"])
                self.assertEqual(len(profile["nodes"]), 3)
                raise ConfigError("synthetic native validation failed")
            publisher.create.side_effect = publish
            try:
                with patch("yomesh.quick_setup.fetch_subscription", side_effect=fetched_fixture):
                    result = quick_create_space(spaces, publisher, {"subscription_url": "https://fixture.example/sub", "regions": ["hk", "us", "sg"], "rules": ["ai"]}, "http://127.0.0.1:8767")
                self.assertTrue(seen)
                self.assertFalse(any(node["source"] == "manual" for node in seen))
                self.assertEqual(result["summary"]["nodes"], 3)
                self.assertEqual(result["status"], "draft")
                self.assertIsNone(result["subscription_link"])
                self.assertIn("validation failed", result["publication_error"])
            finally:
                spaces.close()


class ImportFixes(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.spaces = ConfigurationSpaces(self.temporary.name)
        self.config = {"proxies": [{"name": "fixture", "type": "trojan", "server": "node.example.test", "port": 443, "password": "synthetic-password"}], "proxy-groups": [{"name": "Proxy", "type": "select", "proxies": ["fixture"]}], "rules": ["MATCH,DIRECT"]}

    def tearDown(self):
        self.spaces.close()
        self.temporary.cleanup()

    def import_config(self, config, mode="portable"):
        import yaml
        return import_configuration_space(self.spaces, {"name": "audit", "mode": mode, "content": yaml.safe_dump(config), "filename": "fixture.yaml", "revision": self.spaces.state["revision"]}, ROOT)

    def test_auto_preserves_unsupported_rules_in_native_profile(self):
        self.config["rules"] = ["GEOIP,CN,DIRECT", "MATCH,DIRECT"]
        result = self.import_config(self.config, "auto")
        native = self.spaces.workspace_snapshot(result["id"])["profile"]["native_configuration"]
        self.assertEqual([row["text"] for row in native["policies"]], self.config["rules"])

    def test_lossy_portable_rules_and_unknown_targets_fail_without_creating_space(self):
        for rule in ("GEOIP,CN,DIRECT", "DOMAIN,example.com,missing", "IP-CIDR,192.0.2.0/24,DIRECT", "DOMAIN,example.com,DIRECT,no-resolve"):
            with self.subTest(rule=rule):
                before = self.spaces.snapshot()
                self.config["rules"] = [rule, "MATCH,DIRECT"]
                with self.assertRaises(ConfigError):
                    self.import_config(self.config)
                self.assertEqual(before, self.spaces.snapshot())

    def test_direct_fallback_regex_commas_and_intervening_rule_order_are_preserved(self):
        self.config["rules"] = [r"DOMAIN-REGEX,^a{1,3}\.example$,Proxy", "DOMAIN,first.example,DIRECT", "DOMAIN,second.example,Proxy", "MATCH,DIRECT"]
        result = self.import_config(self.config)
        profile = self.spaces.workspace_snapshot(result["id"])["profile"]
        self.assertEqual(profile["fallback"], "DIRECT")
        self.assertEqual(len(profile["policies"]), 3)
        self.assertEqual(profile["library"][0]["entries"], [r"DOMAIN-REGEX,^a{1,3}\.example$"])
        output = compile_bundle(profile, component="proxy")["files"]["mihomo.yaml"]
        self.assertEqual(output["rules"][-1], "MATCH,DIRECT")
        self.assertIn("first.example", output["rules"][1])
        self.assertIn("second.example", output["rules"][2])

    def test_failed_localdns_conversion_is_reported_without_creating_space(self):
        before = self.spaces.snapshot()
        import yaml
        with self.assertRaisesRegex(ConfigError, "LocalDNS"):
            import_configuration_space(self.spaces, {"name": "audit", "mode": "portable", "content": yaml.safe_dump(self.config), "filename": "fixture.yaml", "localdns_filename": "localdns.json", "localdns_content": '{"dns":{}}', "revision": before["revision"]}, ROOT)
        self.assertEqual(before, self.spaces.snapshot())

    def test_singbox_keeps_and_between_domain_and_ip_and_preserves_direct_final(self):
        config = {"outbounds": [{"type": "direct", "tag": "direct"}], "route": {"rules": [{"domain_suffix": ["example.test"], "ip_cidr": ["192.0.2.0/24"], "outbound": "direct"}], "final": "direct"}}
        profile = _convert_singbox_to_profile(config, {}, {}, ROOT)
        policy = profile["policies"][0]
        self.assertEqual(policy["group_operator"], "and")
        self.assertEqual(len(policy["condition_groups"]), 2)
        self.assertEqual(profile["fallback"], "DIRECT")
        config["route"]["rules"][0]["outbound"] = "missing"
        with self.assertRaises(ConfigError):
            _convert_singbox_to_profile(config, {}, {}, ROOT)
        config["route"]["rules"][0]["outbound"] = "DIRECT"
        with self.assertRaises(ConfigError):
            _convert_singbox_to_profile(config, {}, {}, ROOT)
        config["route"]["rules"] = [{"domain_suffix": ["example.test"], "network": "udp", "outbound": "direct"}]
        with self.assertRaises(ConfigError):
            _convert_singbox_to_profile(config, {}, {}, ROOT)


class InstallerFixes(unittest.TestCase):
    def installer(self):
        spec = importlib.util.spec_from_file_location("audit_hermes_installer", ROOT / "scripts/hermes_installer.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_pinned_bridge_hash_is_checked_before_writing_any_installation_file(self):
        installer = self.installer()
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"YOMESH_URL": "http://127.0.0.1:8767", "YOMESH_TOKEN": "ymh_synthetic_audit_token", "YOMESH_MCP_SHA256": "0" * 64}):
            root = Path(temporary)
            with patch.object(installer.Path, "home", return_value=root), patch.object(installer, "build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(b"tampered synthetic bridge")
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    installer.main()
            self.assertEqual(list(root.iterdir()), [])

    def test_existing_hermes_servers_are_preserved_and_stale_yomesh_is_updated(self):
        installer = self.installer()
        with tempfile.TemporaryDirectory() as temporary:
            config = Path(temporary) / "config.yaml"
            config.write_text('mcp_servers:\n  other:\n    command: fixture\n  yomesh:\n    command: stale\n', encoding="utf-8")
            text = installer._hermes_yaml(config, Path("/fixture/bridge.py"), Path("/fixture/config.json"))
            import yaml
            result = yaml.safe_load(text)
            self.assertEqual(result["mcp_servers"]["other"]["command"], "fixture")
            self.assertEqual(result["mcp_servers"]["yomesh"]["command"], "python3")
            config.write_text("mcp_servers: [", encoding="utf-8")
            with self.assertRaises(ValueError):
                installer._hermes_yaml(config, Path("/fixture/bridge.py"), Path("/fixture/config.json"))
            self.assertEqual(config.read_text(encoding="utf-8"), "mcp_servers: [")

    def test_valid_pinned_bridge_installs_offline_and_keeps_other_servers(self):
        installer = self.installer()
        bridge = b"# synthetic bridge fixture; not executed by this test\n"
        with tempfile.TemporaryDirectory() as temporary, patch.dict(os.environ, {"YOMESH_URL": "http://127.0.0.1:8767", "YOMESH_TOKEN": "ymh_synthetic_audit_token", "YOMESH_MCP_SHA256": hashlib.sha256(bridge).hexdigest()}):
            root = Path(temporary)
            (root / ".hermes").mkdir()
            config = root / ".hermes/config.yaml"
            config.write_text("mcp_servers:\n  other:\n    command: fixture\n", encoding="utf-8")
            with patch.object(installer.Path, "home", return_value=root), patch.object(installer, "build_opener") as opener, patch.object(installer.shutil, "which", return_value=None), redirect_stdout(io.StringIO()):
                opener.return_value.open.return_value = io.BytesIO(bridge)
                self.assertEqual(installer.main(), 0)
            self.assertEqual((root / ".local/share/yomesh/yomesh_hermes_mcp.py").read_bytes(), bridge)
            self.assertEqual((root / ".config/yomesh/hermes.token").read_text().strip(), "ymh_synthetic_audit_token")
            import yaml
            result = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(result["mcp_servers"]["other"]["command"], "fixture")
            self.assertEqual(result["mcp_servers"]["yomesh"]["command"], "python3")
            self.assertEqual(list(root.rglob(".yomesh-*")), [])

    def test_bootstrap_refuses_changed_installer_before_execution(self):
        expected = b"print('synthetic installer executed')\n"
        class Download(BaseHTTPRequestHandler):
            body = b"print('tampered installer executed')\n"
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(self.body)
            def log_message(self, *args):
                pass
        with ThreadingHTTPServer(("127.0.0.1", 0), Download) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                command = installation_command(f"http://127.0.0.1:{server.server_port}", "ymh_synthetic_audit_token", {"installer_sha256": hashlib.sha256(expected).hexdigest(), "bridge_sha256": "0" * 64})
                parts = shlex.split(command)
                environment = {**os.environ, **dict(part.split("=", 1) for part in parts[:3])}
                result = subprocess.run([sys.executable, "-c", parts[-1]], env=environment, capture_output=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn(b"installer executed", result.stdout)
                self.assertNotIn(b"ymh_synthetic_audit_token", result.stderr)
                Download.body = expected
                result = subprocess.run([sys.executable, "-c", parts[-1]], env=environment, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(b"synthetic installer executed", result.stdout)
            finally:
                server.shutdown()
