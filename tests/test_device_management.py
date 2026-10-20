"""Device reads stay private and device APIs require a protected workbench."""
import base64
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from test_native_configuration import fixture

from yomesh.device_management import DeviceManagement
from yomesh.device_registry import DeviceRegistry
from yomesh.model import ConfigError
from yomesh.server import handler_for
from yomesh.spaces import ConfigurationSpaces


class ReadOnlyTransport:
    def __init__(self, files):
        self.files = files
        self.calls = []
        self.prepared = None
        self.before_release = None
        self.running = False

    def call(self, device, operation, payload=None):
        self.calls.append(operation)
        manifest = {name: hashlib.sha256(body).hexdigest() for name, body in self.files.items()
                    if not name.startswith("localdns/generated/")}
        if operation == "read":
            return {"files": {name: base64.b64encode(body).decode() for name, body in self.files.items()},
                    "manifest": manifest, "manager": "openrc"}
        if operation == "manifest":
            return {"manifest": manifest}
        if operation == "probe":
            return {"executor": 1, "mihomo": True, "localdns": True}
        if operation == "prepare":
            self.prepared = payload
            self.before_release = dict(self.files)
            return {"status": "prepared", "validation": {"mihomo": "passed", "localdns": "passed"}}
        if operation == "commit":
            self.running = True
            return {"status": "running"}
        if operation == "status":
            if self.running:
                with zipfile.ZipFile(io.BytesIO(base64.b64decode(self.prepared["bundle"]))) as archive:
                    self.files = {name: archive.read(name) for name in archive.namelist()}
                self.running = False
            return {"status": "succeeded"}
        if operation == "rollback":
            if self.before_release is None:
                raise AssertionError("no prepared release to roll back")
            self.files = self.before_release
            return {"status": "rolled_back"}
        raise AssertionError("unexpected device operation: " + operation)

    def host_key(self, host, port):
        return "SHA256:" + "A" * 43


class DeviceReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        native = fixture()
        native["mihomo"]["proxies"] = [{"name": "private-node", "type": "ss", "server": "example.test",
                                        "port": 443, "cipher": "aes-128-gcm", "password": "private-secret"}]
        main = {**native["mihomo"], "rules": [row["text"] for row in native["policies"]]}
        files = {"mihomo.yaml": yaml.safe_dump(main, allow_unicode=True).encode(),
                 **{name: text.encode() for name, text in native["documents"].items()},
                 **{name: base64.b64decode(text) for name, text in native["assets"].items()}}
        files["localdns/rules/manual/bom.txt"] = b"\xef\xbb\xbfexample.com\n"
        self.transport = ReadOnlyTransport(files)
        self.spaces = ConfigurationSpaces(self.root)
        self.registry = DeviceRegistry(self.root, self.transport)
        self.ident = "device_" + "a" * 32
        self.registry.state["devices"].append({"id": self.ident, "name": "测试设备", "host": "127.0.0.1",
                                                "port": 22, "username": "root", "host_key": "SHA256:" + "A" * 43,
                                                "space_id": None, "baseline": None, "baseline_digest": None,
                                                "mihomo_home": "/etc/mihomo"})
        self.manager = DeviceManagement(self.root, self.spaces, self.registry)

    def tearDown(self):
        self.spaces.close()
        self.temp.cleanup()

    def test_preview_is_stable_and_does_not_return_credentials(self):
        preview = self.manager.preview_pull(self.ident)
        self.assertTrue(preview["different"])
        self.assertNotIn("_native", preview)
        self.assertNotIn("private-secret", json.dumps(preview))
        result = self.manager.pull(self.ident, preview["remote_digest"])
        self.assertEqual(result["space_id"], self.registry.device(self.ident)["space_id"])
        native = self.spaces.workspace_snapshot(result["space_id"])["profile"]["native_configuration"]
        self.assertEqual(native["documents"]["localdns/rules/manual/bom.txt"].encode(),
                         b"\xef\xbb\xbfexample.com\n")
        with self.assertRaisesRegex(ConfigError, "无需发布"):
            self.manager.plan(self.ident, self.spaces.workspace_snapshot(result["space_id"])["revision"])
        repeated = self.manager.preview_pull(self.ident)
        self.assertFalse(repeated["different"])
        self.assertEqual(repeated["remote_digest"], preview["remote_digest"])
        self.assertNotIn("private-secret", json.dumps(repeated))
        self.assertNotIn("prepare", self.transport.calls)
        self.assertNotIn("commit", self.transport.calls)

    def test_prepare_approval_and_remote_conflict_are_separate_gates(self):
        preview = self.manager.preview_pull(self.ident)
        space_id = self.manager.pull(self.ident, preview["remote_digest"])["space_id"]
        snapshot = self.spaces.workspace_snapshot(space_id)
        snapshot["profile"]["native_configuration"]["mihomo"]["mixed-port"] = 17891
        changed = self.spaces.save_profile(snapshot["profile"], snapshot["revision"], space_id)
        release = self.manager.plan(self.ident, changed["revision"])
        self.assertEqual(release["status"], "prepared")
        self.assertTrue(any(item["file"] == "mihomo.yaml" for item in release["changes"]))
        self.assertNotIn("commit", self.transport.calls)
        with self.assertRaisesRegex(ConfigError, "不一致"):
            self.manager.approve(release["id"], self.ident, "0" * 64, changed["revision"])
        self.assertNotIn("commit", self.transport.calls)
        accepted = self.manager.approve(release["id"], self.ident, release["bundle_sha256"], changed["revision"])
        self.assertEqual(accepted["status"], "running")
        self.assertEqual(self.manager.status(release["id"])["status"], "succeeded")
        self.assertEqual(self.registry.device(self.ident)["refs"], release["refs"])

        self.transport.files["mihomo.yaml"] += b"\n# external edit\n"
        with self.assertRaisesRegex(ConfigError, "设备配置已变化"):
            self.manager.plan(self.ident, changed["revision"])

    def test_binding_existing_space_requires_explicit_overwrite_on_first_pull(self):
        catalog = self.spaces.snapshot()
        imported = self.spaces.import_native(fixture(), "已有原生空间", catalog["revision"])
        self.manager.bind(self.ident, imported["id"], self.registry.listing()["revision"])
        preview = self.manager.preview_pull(self.ident)
        self.assertTrue(preview["local_changed_since_pull"])
        with self.assertRaisesRegex(ConfigError, "草稿已有修改"):
            self.manager.pull(self.ident, preview["remote_digest"], preview["workspace_revision"])
        result = self.manager.pull(self.ident, preview["remote_digest"], preview["workspace_revision"], replace=True)
        self.assertEqual(result["space_id"], imported["id"])


class DeviceApiAuthTests(unittest.TestCase):
    def test_unprotected_workbench_cannot_manage_devices(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(root, 8767))
            server.RequestHandlerClass.log_message = lambda *args: None
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                for method, path, body in (("GET", "/api/devices", None),
                                           ("POST", "/api/devices/host-key", {"host": "127.0.0.1", "port": 22}),
                                           ("POST", "/api/devices", {"device": {}, "revision": 0})):
                    with self.subTest(method=method, path=path):
                        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                        connection.request(method, path, json.dumps(body).encode() if body is not None else None,
                                           {"Host": "127.0.0.1:8767", "Content-Type": "application/json"})
                        response = connection.getresponse()
                        self.assertEqual(response.status, 403)
                        self.assertIn("启用密码保护", response.read().decode())
                        connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                server.RequestHandlerClass.space_catalog.close()

    def test_authenticated_device_read_imports_a_separate_native_space(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            native = fixture()
            main = {**native["mihomo"], "rules": [row["text"] for row in native["policies"]]}
            files = {"mihomo.yaml": yaml.safe_dump(main, allow_unicode=True).encode(),
                     **{name: text.encode() for name, text in native["documents"].items()},
                     **{name: base64.b64decode(text) for name, text in native["assets"].items()}}
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(root, 8767))
            handler = server.RequestHandlerClass
            handler.log_message = lambda *args: None
            handler.device_management.registry.transport = ReadOnlyTransport(files)
            handler.auth_store.update_credentials("", "admin", "fixture-password")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, body=None, cookie=None):
                connection = HTTPConnection("127.0.0.1", server.server_port, timeout=8)
                headers = {"Host": "127.0.0.1:8767", "Content-Type": "application/json"}
                if cookie:
                    headers["Cookie"] = cookie
                connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
                response = connection.getresponse()
                result = response.status, dict(response.getheaders()), json.loads(response.read())
                connection.close()
                return result

            try:
                status, headers, _ = request("POST", "/api/auth/login", {"username": "admin", "password": "fixture-password"})
                self.assertEqual(status, 200)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                settings = {"name": "测试设备", "host": "192.0.2.7", "port": 22, "username": "root",
                            "host_key": "SHA256:" + "A" * 43, "mihomo_home": "/etc/mihomo",
                            "mihomo_config": "/etc/mihomo/mihomo.yaml", "mihomo_binary": "/usr/local/bin/mihomo",
                            "localdns_root": "/opt/localdns", "localdns_binary": "/opt/localdns/localdns",
                            "manager": "openrc", "services": {"mihomo": "mihomo", "localdns": "localdns"}}
                status, _, added = request("POST", "/api/devices", {"device": settings, "revision": 0}, cookie)
                self.assertEqual(status, 200)
                self.assertTrue(added["public_key"].startswith("ssh-rsa "))
                ident = added["device"]["id"]
                self.assertEqual(request("GET", "/api/devices", cookie=cookie)[2]["devices"][0]["id"], ident)
                status, _, preview = request("GET", f"/api/devices/{ident}/preview-pull", cookie=cookie)
                self.assertEqual(status, 200)
                self.assertNotIn("_native", preview)
                status, _, imported = request("POST", f"/api/devices/{ident}/pull",
                                              {"remote_digest": preview["remote_digest"]}, cookie)
                self.assertEqual(status, 200)
                self.assertNotEqual(imported["space_id"], "default")
                self.assertEqual(handler.space_catalog.workspace_snapshot(imported["space_id"])["profile"]["native_configuration"]["format"], "mihomo-localdns-v1")
                self.assertFalse(request("GET", f"/api/devices/{ident}/preview-pull", cookie=cookie)[2]["different"])
                catalog_revision = request("GET", "/api/spaces", cookie=cookie)[2]["revision"]
                self.assertEqual(request("POST", "/api/spaces/delete", {"id": imported["space_id"], "revision": catalog_revision}, cookie)[0], 422)
                device_revision = request("GET", "/api/devices", cookie=cookie)[2]["revision"]
                self.assertEqual(request("POST", f"/api/devices/{ident}/unbind", {"revision": device_revision}, cookie)[0], 200)
                self.assertEqual(request("POST", "/api/spaces/delete", {"id": imported["space_id"], "revision": catalog_revision}, cookie)[0], 200)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                handler.space_catalog.close()
