"""Independent DNS editing and actual MosDNS queries against private fixtures."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError, Profile
from yomesh.mosdns import checked, defaults, refresh_rule, refresh_ip_source
from yomesh.native import find_core, validate_bundle, write_json
from yomesh.server import handler_for
from yomesh.spaces import ConfigurationSpaces
from yomesh.workspace import Workspace
from test_local_dns import dns_reply, free_port, wait_listen
from test_native_configuration import fixture


def profile(config):
    return {"schema_version": 1, "fallback": "DIRECT", "mosdns": config}


def rule(ident="one", **values):
    return {"id": ident, "name": ident, "enabled": True, "domains": ["+.example.com"], "clients": [], "qtypes": [], "action": "local", **values}


class MosDNSTests(unittest.TestCase):
    def test_independent_dns_ignores_broken_proxy_and_preserves_native_objects(self):
        native = fixture()
        native["policies"][0]["text"] = "RULE-SET,missing,DIRECT"
        value = {**profile(defaults()), "native_configuration": native}
        original = deepcopy(value)
        for mode in ("inline", "local", "remote"):
            bundle = compile_bundle(value, "sing-box", component="dns", rule_output=mode)
            self.assertEqual(set(bundle["manifest"]["versions"]), {"mosdns"})
            self.assertFalse(any(p.startswith("localdns/") for p in bundle["files"]))
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        self.assertEqual(value, original)
        with self.assertRaises(ConfigError):
            compile_bundle(value, component="proxy", rule_output="remote")

    def test_profile_and_draft_workspace_validate_same_mosdns_model(self):
        config = defaults()
        config["port"] = 53
        config["listen"] = "::"
        value = profile(config)
        self.assertEqual(Profile.for_dns(value).data["mosdns"]["port"], 53)
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            draft = workspace.snapshot()["profile"]
            draft["mosdns"] = config
            saved = workspace.save_profile(draft, 0)
            self.assertEqual(saved["profile"]["mosdns"], checked(config))
            config["cache"]["size"] = -1
            draft["mosdns"] = config
            with self.assertRaises(ConfigError):
                workspace.save_profile(draft, saved["revision"])
            self.assertEqual(workspace.revision(), saved["revision"])

    def test_combined_package_equals_independent_outputs_and_does_not_infer_socks(self):
        config = defaults(); config["port"] = 5353
        config["groups"]["remote"]["upstreams"][0]["socks5"] = "192.0.2.1:1080"
        value = {**profile(config), "base_configs": {"sing-box": '{"inbounds":[]}'}}
        both = compile_bundle(value, "sing-box", component="both", rule_output="local")
        proxy = compile_bundle(value, "sing-box", component="proxy", rule_output="local")
        dns = compile_bundle(value, "sing-box", component="dns", rule_output="local")
        self.assertEqual(both["files"]["sing-box.json"], proxy["files"]["sing-box.json"])
        self.assertEqual(both["files"]["mosdns.yaml"], dns["files"]["mosdns.yaml"])
        self.assertEqual(both["manifest"]["versions"], {"sing-box": "1.14.3", "mosdns": "5.3.4"})
        self.assertEqual(validate_bundle(both, ROOT)["status"], "passed")

    def test_invalid_or_ambiguous_options_do_not_silently_drop_fields(self):
        candidates = []
        for changes in ({"unknown": True}, {"port": True}, {"udp": False, "tcp": False}, {"ttl_min": 60, "ttl_max": 30}, {"blocked_qtypes": [True]}, {"listen": "example.com"}):
            candidates.append({**defaults(), **changes})
        c = defaults(); c["rules"] = [rule(clients=["127.0.0.1/32"])]; candidates.append(c)
        c = defaults(); c["groups"]["remote"]["upstreams"][0]["addr"] = "udp://1.1.1.1"; candidates.append(c)
        c = defaults(); c["groups"]["ecs"]["ecs"] = "0.0.0.0/0"; candidates.append(c)
        c = defaults(); c["default"] = "proxy"; candidates.append(c)
        c = defaults(); c["groups"]["proxy"]["enabled"] = True; candidates.append(c)
        c = defaults(); c["api"] = "127.0.0.1:1053"; candidates.append(c)
        for config in candidates:
            with self.subTest(config=list(config)), self.assertRaises(ConfigError):
                checked(config)

    def test_udp_only_and_tcp_only_native_checks_isolate_api_and_cache_paths(self):
        for transport in ("udp", "tcp"):
            c = defaults(); c["udp"] = transport == "udp"; c["tcp"] = transport == "tcp"
            c["port"] = 53; c["listen"] = "0.0.0.0"; c["api"] = "127.0.0.1:1"
            c["cache"]["persist"] = True
            bundle = compile_bundle(profile(c), component="dns")
            original = deepcopy(bundle["files"])
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
            self.assertEqual(bundle["files"], original)

    def test_custom_native_mode_preserves_unknown_fields_and_rejects_bad_plugin(self):
        c = defaults()
        raw = compile_bundle(profile(c), component="dns")["files"]["mosdns.yaml"]
        raw["log"]["production"] = True
        c.update(mode="custom", custom_config=raw)
        bundle = compile_bundle(profile(c), component="dns", rule_output="local")
        self.assertEqual(bundle["files"]["mosdns.yaml"], raw)
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        c["custom_config"]["plugins"].insert(0, {"tag": "bad", "type": "not_a_plugin"})
        with self.assertRaises(ConfigError):
            validate_bundle(compile_bundle(profile(c), component="dns"), ROOT)

    def test_rule_source_mixed_ip_or_adguard_exceptions_are_rejected_atomically(self):
        source = {"type": "url", "url": "https://example.com/domains", "format": "classical", "auto_update": False}
        r = rule(source=source)
        original = deepcopy(r)
        with patch("yomesh.rule_sources.fetch", return_value=b"DOMAIN-SUFFIX,example.org\nIP-CIDR,192.0.2.0/24\n"):
            with self.assertRaises(ConfigError):
                refresh_rule(ROOT, r)
        self.assertEqual(r, original)
        with patch("yomesh.rule_sources.fetch", return_value=b"DOMAIN-SUFFIX,example.org\n"):
            self.assertEqual(refresh_rule(ROOT, r)["domains"], ["domain:example.org"])
        r["source"]["format"] = "adguard"
        with patch("yomesh.rule_sources.fetch", return_value=b"@@||example.org^"):
            with self.assertRaises(ConfigError):
                refresh_rule(ROOT, r)

    def test_scheduler_refreshes_enabled_sources_and_retains_snapshot_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            spaces = ConfigurationSpaces(directory)
            spaces.mosdns_updates.stop.set()
            # Invoke the scheduler deterministically without its background loop.
            spaces.mosdns_updates.stop.clear()
            config = defaults(); config["rules"] = [rule(source={"type": "url", "url": "https://example.com/rules", "format": "domain", "auto_update": True, "interval_minutes": 1440})]
            try:
                snap = spaces.workspace_snapshot()
                snap["profile"]["mosdns"] = config
                spaces.save_profile(snap["profile"], snap["revision"])
                with patch("yomesh.rule_sources.fetch", return_value=b"+.updated.example\n"):
                    spaces.mosdns_updates.run_due(now=100)
                snap = spaces.workspace_snapshot()
                self.assertEqual(snap["profile"]["mosdns"]["rules"][0]["domains"], ["domain:updated.example"])
                before = deepcopy(snap["profile"])
                with patch("yomesh.rule_sources.fetch", side_effect=ConfigError("unavailable")):
                    spaces.mosdns_updates.run_due(now=10**12)
                snap = spaces.workspace_snapshot()
                self.assertEqual(snap["profile"], before)
                self.assertEqual(snap["mosdns_status"]["default:one"]["state"], "error")
            finally:
                spaces.close()

    def test_geoip_sources_keep_all_cidrs_and_use_native_ip_set_files(self):
        source = {"id": "geo", "name": "geo", "enabled": True, "entries": [], "source": {"type": "url", "url": "https://example.com/ips", "format": "ipcidr", "auto_update": False}}
        with patch("yomesh.rule_sources.fetch", return_value=b"192.0.2.0/24\n2001:db8::/32\n"):
            updated = refresh_ip_source(ROOT, source)
        self.assertEqual(updated["entries"], ["192.0.2.0/24", "2001:db8::/32"])
        self.assertEqual(source["entries"], [])
        with patch("yomesh.rule_sources.fetch", return_value=b"192.0.2.0/24\nexample.org\n"):
            with self.assertRaises(ConfigError):
                refresh_ip_source(ROOT, source)
        c = defaults(); c["fallback"].update(enabled=True, sources=[updated]); c["default"] = "fallback"
        bundle = compile_bundle(profile(c), component="dns", rule_output="local")
        self.assertEqual(bundle["files"]["rules/mosdns_fallback_ips.txt"], "192.0.2.0/24\n2001:db8::/32\n")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_shared_space_dns_is_independent_and_copy_preserves_it(self):
        with tempfile.TemporaryDirectory() as directory:
            spaces = ConfigurationSpaces(directory)
            try:
                spaces.save_mosdns(defaults(), 0)
                catalog = spaces.save({"name": "another", "proxy": "mihomo", "rule_output": "inline"}, spaces.state["revision"])
                ident = next(s["id"] for s in catalog["spaces"] if s["name"] == "another")
                snap = spaces.workspace_snapshot(ident)
                cfg = deepcopy(snap["profile"]["mosdns"]); cfg["port"] = 5353
                original = spaces.workspace_snapshot("default")["profile"]
                spaces.save_mosdns(cfg, snap["revision"], ident)
                self.assertEqual(spaces.workspace_snapshot("default")["profile"], original)
                self.assertEqual(spaces.workspace_snapshot(ident)["profile"]["mosdns"]["port"], 5353)
                copied = spaces.save({"name": "copy", "proxy": "mihomo", "rule_output": "inline"}, spaces.state["revision"], copy_from=ident)
                copy_id = next(s["id"] for s in copied["spaces"] if s["name"] == "copy")
                self.assertEqual(spaces.workspace_snapshot(copy_id)["profile"]["mosdns"]["port"], 5353)
            finally:
                spaces.close()

    def test_native_dns_save_preserves_proxy_and_localdns_and_copy_keeps_mosdns(self):
        with tempfile.TemporaryDirectory() as directory:
            spaces = ConfigurationSpaces(directory)
            try:
                catalog = spaces.import_native(fixture(), "native", spaces.state["revision"])
                ident = next(s["id"] for s in catalog["spaces"] if s["name"] == "native")
                before = spaces.workspace_snapshot(ident)
                after = spaces.save_mosdns(defaults(), before["revision"], ident)
                raw = deepcopy(after["profile"]); raw.pop("mosdns")
                self.assertEqual(raw, before["profile"])
                catalog = spaces.import_native(raw["native_configuration"], "copy", spaces.state["revision"], mosdns=after["profile"]["mosdns"])
                copy_id = next(s["id"] for s in catalog["spaces"] if s["name"] == "copy")
                copied = spaces.workspace_snapshot(copy_id)["profile"]
                self.assertEqual(copied["mosdns"], after["profile"]["mosdns"])
                self.assertEqual(compile_bundle(copied, component="both", rule_output="remote")["manifest"]["dns_engine"], "mosdns")
            finally:
                spaces.close()

    def test_failed_independent_save_rolls_back_profile_and_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            spaces = ConfigurationSpaces(directory)
            try:
                before = spaces.workspace_snapshot()
                with patch.object(spaces.runtime().workspace, '_write', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):
                        spaces.save_mosdns(defaults(), before['revision'])
                self.assertEqual(spaces.workspace_snapshot()['profile'], before['profile'])
                self.assertEqual(spaces.workspace_snapshot()['revision'], before['revision'])
            finally:
                spaces.close()

    def test_actual_queries_order_clients_ecs_caches_hosts_blocking_and_geoip_fallback(self):
        seen = {"local": [], "remote": []}
        def fixture_handler(key, address):
            class DNSFixture(socketserver.BaseRequestHandler):
                def handle(self):
                    query, connection = self.request
                    seen[key].append(query)
                    connection.sendto(dns_reply(query)[:-4] + socket.inet_aton(address), self.client_address)
            return DNSFixture
        with socketserver.ThreadingUDPServer(("127.0.0.1", 0), fixture_handler("local", "127.0.0.42")) as local, socketserver.ThreadingUDPServer(("127.0.0.1", 0), fixture_handler("remote", "192.0.2.44")) as remote, tempfile.TemporaryDirectory() as directory, tempfile.TemporaryFile() as logs:
            for server in (local, remote):
                threading.Thread(target=server.serve_forever, daemon=True).start()
            c = defaults(); c["port"] = free_port(); c["cache"]["enabled"] = False
            for key, server in (("local", local), ("remote", remote), ("ecs", local)):
                c["groups"][key]["enabled"] = True
                c["groups"][key]["upstreams"] = [{"addr": "127.0.0.1:" + str(server.server_address[1]), "enabled": True}]
            c["groups"]["ecs"].update(ecs="203.0.113.0/24", cache={"enabled": True})
            c["hosts"] = ["full:printer.example 192.0.2.10"]
            c["rules"] = [rule("deny", domains=["domain:ads.example"], action="reject"), rule("ecs", domains=["domain:example"], clients=["127.0.0.1/32"], qtypes=[1], action="ecs")]
            c["fallback"].update(enabled=True, response_ips=["192.0.2.0/24"], threshold=20)
            c.update(default="fallback", block_aaaa=True, ttl_min=120, ttl_max=300)
            write_json(Path(directory) / "mosdns.yaml", compile_bundle(profile(c), component="dns")["files"]["mosdns.yaml"])
            process = subprocess.Popen([str(find_core(ROOT, "mosdns")), "start", "-c", str(Path(directory)/"mosdns.yaml"), "-d", directory], stdout=logs, stderr=logs)
            def query(host, typ=1):
                q = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00' + b''.join(bytes([len(p)]) + p.encode() for p in host.split('.')) + b'\x00' + struct.pack('!HH', typ, 1)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                    client.settimeout(3); client.sendto(q, ("127.0.0.1", c["port"])); return client.recv(4096)
            try:
                wait_listen(process, c["port"])
                self.assertEqual(query("ads.example")[3] & 15, 3)
                self.assertIn(socket.inet_aton("192.0.2.10"), query("printer.example"))
                blocked = query("ipv6.example", 28)
                self.assertEqual(blocked[3] & 15, 0); self.assertEqual(blocked[6:8], b'\x00\x00')
                answer = query("ecs.example")
                self.assertIn(socket.inet_aton("127.0.0.42"), answer)
                self.assertIn(b'\x00\x00\x00\x78', answer)  # minimum TTL 120
                count = len(seen["local"])
                query("ecs.example")
                self.assertEqual(len(seen["local"]), count)
                self.assertIn(b'\xcb\x00\x71', seen["local"][0])  # ECS 203.0.113/24
                self.assertIn(socket.inet_aton("192.0.2.44"), query("unlisted.test"))
                self.assertTrue(seen["remote"])
            finally:
                process.terminate(); process.wait(timeout=5)
                local.shutdown(); remote.shutdown()

    def test_http_save_preview_check_and_export_share_model_without_changing_proxy(self):
        with tempfile.TemporaryDirectory() as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
            handler = handler_for(Path(directory), server.server_port)
            class Quiet(handler):
                def log_message(self, *args): pass
            server.RequestHandlerClass = Quiet
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(path, body=None):
                data = json.dumps(body).encode() if body is not None else None
                return urlopen(Request(f"http://127.0.0.1:{server.server_port}" + path, data=data, headers={"Content-Type": "application/json"}), timeout=30)
            try:
                config = defaults()
                with request('/api/workspace') as response: original = json.load(response)
                with request('/api/workspace/mosdns/save', {"config": config, "revision": original["revision"]}) as response: saved = json.load(response)
                before = deepcopy(original["profile"]); after = deepcopy(saved["profile"]); after.pop('mosdns')
                self.assertEqual(after, before)
                body = {"config": config, "revision": saved["revision"]}
                with patch('yomesh.native.find_core', side_effect=lambda root, name: find_core(ROOT, name)):
                    with request('/api/workspace/mosdns/check', body) as response: check = json.load(response)
                    self.assertEqual(check['validation']['status'], 'passed')
                    with request('/api/export', {"use_workspace": True, "component": "dns", "rule_output": "local"}) as response: archive = zipfile.ZipFile(io.BytesIO(response.read()))
                self.assertEqual(json.loads(archive.read('mosdns.yaml')), check['config'])
                with request('/api/workspace') as response: self.assertEqual(json.load(response)['revision'], saved['revision'])
            finally:
                handler.space_catalog.close(); server.shutdown(); server.server_close()
