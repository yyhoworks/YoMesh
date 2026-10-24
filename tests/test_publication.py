"""Public subscriptions preserve routing and only expose scoped, revocable assets."""
import io
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_native_configuration import fixture
from yomesh.native import find_core, validate_bundle
from yomesh.publication import Publications, public_base
from yomesh.server import handler_for


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.validation = patch("yomesh.publication.validate_bundle", side_effect=lambda bundle, root: validate_bundle(bundle, ROOT))
        self.validation.start()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        self.handler = handler_for(self.root, self.server.server_port)
        class Quiet(self.handler):
            def log_message(self, *args):
                pass
        self.server.RequestHandlerClass = Quiet
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)
        self.spaces = self.handler.space_catalog
        self.native = fixture()
        self.native.update(format="mihomo-v1", documents={}, assets={})
        self.native["mihomo"]["mixed-port"] = 0
        self.save(self.native)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.spaces.close()
        self.validation.stop()
        self.temporary.cleanup()

    def save(self, native):
        state = self.spaces.workspace_snapshot("default")
        self.spaces.save_profile({"schema_version": 1, "fallback": "DIRECT", "native_configuration": native}, state["revision"], "default")

    def request(self, path, body=None, headers=None, raw=False):
        request = Request(self.base + path, data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urlopen(request, timeout=40) as response:
                content = response.read()
                return response.status, content if raw else json.loads(content)
        except HTTPError as error:
            return error.code, json.load(error)

    def create(self, mode="site", **extra):
        body = {"base_url": self.base, "proxy": "mihomo", "rule_address": mode, "revision": self.spaces.workspace_snapshot("default")["revision"], **extra}
        status, result = self.request("/api/workspace/subscriptions", body)
        self.assertEqual(status, 200, result)
        return result

    def main(self, record):
        status, raw = self.request(urlsplit(record["url"]).path, raw=True)
        self.assertEqual(status, 200, raw)
        return yaml.safe_load(raw)

    def test_original_and_site_addresses_preserve_profile_rules_and_provider_parameters(self):
        value = deepcopy(self.native)
        provider = {"type": "http", "behavior": "classical", "format": "yaml", "url": "https://rules.example.test/list", "path": "rules/one.yaml", "interval": 1200, "header": {"User-Agent": ["original-agent"]}}
        value["mihomo"]["rule-providers"]["one"] = provider
        value["documents"]["rules/one.yaml"] = "rules:\n- DOMAIN-SUFFIX,example.test,DIRECT\n"
        self.save(value)
        before = self.spaces.workspace_snapshot("default")["profile"]
        original, hosted = self.main(self.create("original")), self.main(self.create("site"))
        self.assertEqual(original["rule-providers"]["one"], provider)
        target = hosted["rule-providers"]["one"]
        self.assertTrue(target["url"].startswith(self.base + "/s/"))
        self.assertNotIn("header", target)
        for field in ("behavior", "format", "interval"):
            self.assertEqual(target[field], provider[field])
        self.assertEqual(original["rules"], hosted["rules"])
        self.assertEqual(self.spaces.workspace_snapshot("default")["profile"], before)

    def test_real_core_loads_hosted_rule_from_only_downloaded_main_file(self):
        record = self.create()
        config = self.main(record)
        self.assertEqual(config["rule-providers"]["one"]["type"], "http")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config["external-controller"] = "127.0.0.1:" + str(port)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "config.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
            process = subprocess.Popen([str(find_core(ROOT, "mihomo")), "-d", directory, "-f", str(path / "config.yaml")], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for attempt in range(100):
                    try:
                        with urlopen("http://127.0.0.1:" + str(port) + "/providers/rules", timeout=1) as response:
                            if json.load(response)["providers"]["one"]["ruleCount"] == 1:
                                break
                    except OSError:
                        pass
                    time.sleep(0.05)
                else:
                    self.fail("Standalone subscription did not download and load its hosted rule")
            finally:
                process.terminate()
                process.wait(timeout=10)

    def test_link_survives_restart_and_can_be_revoked_without_touching_profile(self):
        record = self.create()
        token = record["url"].split("/s/")[1].split("/")[0]
        service = Publications(self.root, self.spaces)
        self.assertIsNotNone(service.read(token, filename="mihomo.yaml"))
        before = self.spaces.workspace_snapshot("default")
        self.assertEqual(self.request("/api/workspace/subscriptions/revoke", {"id": record["id"]})[0], 200)
        self.assertIsNone(Publications(self.root, self.spaces).read(token))
        self.assertEqual(self.request(urlsplit(record["url"]).path, raw=True)[0], 404)
        self.assertEqual(before, self.spaces.workspace_snapshot("default"))

    def test_saved_profile_updates_are_live_and_stale_create_is_rejected(self):
        record = self.create()
        revision = self.spaces.workspace_snapshot("default")["revision"]
        value = deepcopy(self.native)
        value["policies"].insert(0, {"id": "extra", "text": "DST-PORT,123,REJECT", "enabled": True})
        self.save(value)
        self.assertEqual(self.main(record)["rules"][0], "DST-PORT,123,REJECT")
        code, error = self.request("/api/workspace/subscriptions", {"base_url": self.base, "revision": revision})
        self.assertEqual(code, 409)

    def test_a_new_broken_profile_cannot_bypass_native_export_gate(self):
        record = self.create()
        value = deepcopy(self.native)
        value["policies"][0]["text"] = "RULE-SET,missing,DIRECT"
        self.save(value)
        status, error = self.request(urlsplit(record["url"]).path)
        self.assertEqual(status, 422)
        self.assertIn("不存在", error["error"])

    def test_links_remain_scoped_when_active_space_changes_and_deleted_space_is_unavailable(self):
        first = self.create()
        catalog = self.spaces.snapshot()
        created = self.spaces.import_native(deepcopy(self.native), "other", catalog["revision"])
        ident = created["id"]
        second_snapshot = self.spaces.workspace_snapshot(ident)
        body = {"base_url": self.base, "proxy": "mihomo", "rule_address": "site", "revision": second_snapshot["revision"]}
        code, second = self.request("/api/workspace/subscriptions", body, {"X-YoMesh-Space": ident})
        self.assertEqual(code, 200, second)
        self.assertNotEqual(first["url"], second["url"])
        expected = self.main(first)
        self.spaces.select(ident, self.spaces.snapshot()["revision"])
        self.assertEqual(self.main(first), expected)
        self.spaces.select("default", self.spaces.snapshot()["revision"])
        self.spaces.delete(ident, self.spaces.snapshot()["revision"])
        self.assertEqual(self.request(urlsplit(second["url"]).path)[0], 404)

    def test_changing_base_keeps_token_and_rewrites_only_generated_addresses(self):
        first = self.create()
        second = self.create(base_url="https://mesh.example.test")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(urlsplit(first["url"]).path, urlsplit(second["url"]).path)
        config = self.main(second)
        self.assertTrue(config["rule-providers"]["one"]["url"].startswith("https://mesh.example.test/"))

    def test_invalid_base_and_asset_paths_do_not_open_management_or_files(self):
        for base in ("https://user:password@example.test", "https://example.test/?q=1", "https://example.test/#x", "file:///etc", "http://example.test/subpath", "http://example.test:bad"):
            with self.subTest(base=base), self.assertRaises(Exception):
                public_base(base)
        record = self.create()
        path = urlsplit(record["url"]).path
        self.assertEqual(self.request(path, headers={"Host": "proxy.example.test"}, raw=True)[0], 200)
        # Host checking was removed; the management API answers any host and
        # still requires login when auth is enabled.
        self.assertEqual(self.request("/api/workspace", headers={"Host": "proxy.example.test"})[0], 200)
        self.assertEqual(self.request(path.replace("mihomo.yaml", "assets/../../workspace.json"))[0], 404)
        self.assertEqual(self.request("/s/" + "a" * 43 + "/mihomo.yaml")[0], 404)

    def test_remote_failure_and_bad_update_fall_back_to_last_validated_cache(self):
        body = {"value": b"rules:\n- DOMAIN-SUFFIX,new.example,DIRECT\n"}
        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body["value"])
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            value = deepcopy(self.native)
            value["mihomo"]["rule-providers"]["one"] = {"type": "http", "url": "http://127.0.0.1:" + str(server.server_port) + "/rules", "path": "rules/one.yaml", "behavior": "classical"}
            value["documents"]["rules/one.yaml"] = "payload:\n- DOMAIN-SUFFIX,old.example\n"
            self.save(value)
            record = self.create()
            url = self.main(record)["rule-providers"]["one"]["url"]
            self.assertEqual(self.request(urlsplit(url).path, raw=True)[1], body["value"])
            good = body["value"]
            body["value"] = b"payload: [null]"
            with patch("yomesh.publication.time.monotonic", return_value=time.monotonic() + 600):
                self.assertEqual(self.request(urlsplit(url).path, raw=True)[1], good)
            # On restart the disk fallback is still available, without storing bodies in metadata.
            token, asset = url.split("/s/")[1].split("/assets/")
            self.assertEqual(Publications(self.root, self.spaces).read(token, asset=asset)[0], good)
        finally:
            server.shutdown()
            server.server_close()

    def test_portable_mihomo_and_singbox_use_same_profile_and_host_local_rules(self):
        profile = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))
        profile["sources"] = []
        for node in profile["nodes"]:
            node["source"] = "manual"
        for group in profile.get("groups", []):
            if group.get("sources"):
                group["sources"] = []
                group["nodes"] = [node["id"] for node in profile["nodes"]]
            if group.get("mode") in {"fallback", "load-balance"}:
                group["mode"] = "url-test"
        before = self.spaces.workspace_snapshot("default")
        self.spaces.save_profile(profile, before["revision"], "default")
        for proxy in ("mihomo", "sing-box"):
            record = self.create(proxy=proxy)
            config = self.main(record)
            providers = config.get("rule-providers", {}).values() if proxy == "mihomo" else config["route"].get("rule_set", [])
            for provider in providers:
                self.assertTrue(provider["url"].startswith(self.base + "/s/"))
                self.assertEqual(self.request(urlsplit(provider["url"]).path, raw=True)[0], 200)
