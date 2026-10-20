import base64
import json
import re
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError, Profile
from yomesh.native import find_core, validate_bundle, write_json
from yomesh.workspace import Workspace
from yomesh.migration import seconds
from test_local_dns import free_port, wait_listen


class MigrationFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Fresh test-only key material, never copied from the user's MR.
        result = subprocess.run([str(find_core(ROOT, "sing-box")), "generate", "tls-keypair", "home.example"], capture_output=True, check=True)
        value = (result.stdout + result.stderr).decode()
        cls.cert = re.search(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", value, re.S).group()
        cls.key = re.search(r"-----BEGIN (?:EC |RSA )?PRIVATE KEY-----.*?-----END (?:EC |RSA )?PRIVATE KEY-----", value, re.S).group()

    def profile(self):
        active = {"id": "proxy", "name": "proxy", "policies": [{"id": "deny", "name": "proxy rejects private", "type": "IP-CIDR", "value": "127.0.0.0/8", "target": "REJECT"}], "fallback": "DIRECT"}
        home = {"id": "home", "name": "home", "policies": [{"id": "local", "name": "home private direct", "type": "IP-CIDR", "value": "127.0.0.0/8", "target": "DIRECT"}], "fallback": "DIRECT"}
        return {"schema_version": 1, "sources": [], "nodes": [], "groups": [], "library": [], "policies": deepcopy(active["policies"]), "fallback": "DIRECT", "active_plan": "proxy", "routing_plans": [active, home], "inbounds": [], "settings": {"mixed_port": free_port(), "dns_port": free_port()}}

    def add_inbounds(self, profile):
        used = {profile["settings"]["mixed_port"], profile["settings"]["dns_port"]}
        for protocol in ("shadowsocks", "trojan", "hysteria2"):
            port = free_port()
            while port in used:
                port = free_port()
            used.add(port)
            entry = {"id": "home_" + protocol, "name": "home " + protocol, "protocol": protocol, "listen": "127.0.0.1", "port": port, "enabled": True, "home": True, "sniff": True, "plan": "home", "password": "fixture"}
            if protocol == "shadowsocks":
                entry.update(method="2022-blake3-aes-256-gcm", password=base64.b64encode(bytes(range(32))).decode())
            else:
                entry.update(certificate=self.cert, private_key=self.key)
            if protocol == "hysteria2":
                entry["alpn"] = ["h3"]
            profile["inbounds"].append(entry)

    def test_mr_duration_preserves_millisecond_tolerance(self):
        self.assertEqual(seconds("5m0s"), 300)
        self.assertEqual(round(seconds("20ms") * 1000), 20)

    def test_classical_or_matches_load_in_all_three_cores(self):
        profile = self.profile()
        profile["library"] = [{"id": "mixed", "name": "mixed conditions", "kind": "classical", "entries": ["DOMAIN,exact.example", "DOMAIN-SUFFIX,example.org", "DOMAIN-KEYWORD,spaceship", r"DOMAIN-REGEX,^regex[0-9]+\.example$", "IP-CIDR,192.168.0.0/16", "IP-CIDR6,fc00::/7"]}]
        policy = {"id": "mixed_policy", "name": "mixed", "type": "RULE-SET", "value": "mixed", "target": "DIRECT"}
        profile["policies"] = [policy]
        profile["routing_plans"][0]["policies"] = [policy]
        for target in ("mihomo", "sing-box"):
            bundle = compile_bundle(profile, target)
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        routes = compile_bundle(profile, "sing-box")["files"]["sing-box.json"]["route"]["rules"]
        self.assertTrue(all(len(set(rule) & {"domain", "domain_suffix", "domain_keyword", "domain_regex", "ip_cidr"}) == 1 for rule in routes))

    def test_group_order_selected_and_tolerance_are_preserved(self):
        profile = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))
        profile["groups"][0].update(order=["n5", "n2"], tolerance=20)
        profile["groups"][1].update(nodes=["n1", "n2"], selected="n2")
        parsed = Profile(profile)
        self.assertEqual(parsed.members("g1")[:2], ["n5", "n2"])
        self.assertEqual(parsed.members("g2")[0], "n2")
        for target in ("mihomo", "sing-box"):
            self.assertEqual(validate_bundle(compile_bundle(profile, target), ROOT)["status"], "passed")

    def test_home_inbound_protocols_and_plan_references_load_natively(self):
        profile = self.profile()
        self.add_inbounds(profile)
        for target in ("mihomo", "sing-box"):
            self.assertEqual(validate_bundle(compile_bundle(profile, target), ROOT)["status"], "passed")
        profile["inbounds"][0]["plan"] = "missing"
        with self.assertRaisesRegex(ConfigError, "规则方案不存在"):
            compile_bundle(profile)

    def test_singbox_compatibility_requires_explicit_choice_and_preserves_workspace(self):
        profile = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))
        group = profile["groups"][0]
        group["mode"] = "fallback"
        original = deepcopy(profile)
        with self.assertRaisesRegex(ConfigError, "无原生 fallback"):
            compile_bundle(profile, "sing-box")
        bundle = compile_bundle(profile, "sing-box", {group["id"]: "url-test"})
        self.assertEqual(profile, original)
        self.assertEqual(bundle["manifest"]["compatibility_choices"][0]["original_mode"], "fallback")
        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        for choices in ({"missing": "url-test"}, {group["id"]: "select"}):
            with self.assertRaises(ConfigError):
                compile_bundle(profile, "sing-box", choices)
        with self.assertRaises(ConfigError):
            compile_bundle(profile, "mihomo", {group["id"]: "url-test"})

    def test_plan_switch_save_restart_and_failed_import_are_atomic(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            profile = self.profile()
            config = {"profile": profile, "routing_plans": profile["routing_plans"], "active_plan": "proxy"}
            state = workspace.import_configuration(config, 0)
            home = workspace.switch_plan("home", state["revision"])
            self.assertEqual(home["profile"]["policies"][0]["id"], "local")
            home["profile"]["policies"][0]["name"] = "edited home policy"
            state = workspace.save_profile(home["profile"], home["revision"])
            workspace.switch_plan("proxy", state["revision"])
            reopened = Workspace(temporary)
            state = reopened.switch_plan("home", reopened.revision())
            self.assertEqual(state["profile"]["policies"][0]["name"], "edited home policy")
            invalid = deepcopy(config)
            invalid["routing_plans"][1]["fallback"] = "missing"
            before = reopened.snapshot()
            with self.assertRaises(ConfigError):
                reopened.import_configuration(invalid, reopened.revision())
            self.assertEqual(reopened.snapshot(), before)

    def test_home_inbound_reaches_lan_while_main_proxy_rule_rejects(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)
                self.send_response(204)
                self.end_headers()
            def log_message(self, *args):
                pass
        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()

        def request(port):
            with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                connection.sendall(f"GET http://127.0.0.1:{http.server_port}/nas HTTP/1.1\r\nHost: 127.0.0.1:{http.server_port}\r\nConnection: close\r\n\r\n".encode())
                try:
                    return connection.recv(4096)
                except ConnectionResetError:
                    return b""  # Native reject may return TCP RST on Windows.
        try:
            for target in ("mihomo", "sing-box"):
                with self.subTest(target=target), tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryFile() as logs:
                    profile = self.profile()
                    self.add_inbounds(profile)
                    bundle = compile_bundle(profile, target)
                    folder = Path(temporary)
                    filename = target + (".yaml" if target == "mihomo" else ".json")
                    write_json(folder / filename, bundle["files"][filename])
                    command = [str(find_core(ROOT, target)), "-d", str(folder), "-f", str(folder / filename)] if target == "mihomo" else [str(find_core(ROOT, target)), "run", "-c", str(folder / filename)]
                    server = subprocess.Popen(command, stdout=logs, stderr=logs, cwd=folder)
                    try:
                        wait_listen(server, profile["settings"]["mixed_port"])
                        before = len(seen)
                        self.assertNotIn(b"204", request(profile["settings"]["mixed_port"]))
                        self.assertEqual(len(seen), before)
                        for inbound in profile["inbounds"]:
                            with self.subTest(protocol=inbound["protocol"]):
                                port = free_port()
                                outbound = {"type": inbound["protocol"], "tag": "home", "server": "127.0.0.1", "server_port": inbound["port"], "password": inbound["password"]}
                                if inbound["protocol"] == "shadowsocks":
                                    outbound["method"] = inbound["method"]
                                else:
                                    outbound["tls"] = {"enabled": True, "server_name": "home.example", "insecure": True}
                                if inbound["protocol"] == "hysteria2":
                                    outbound["tls"]["alpn"] = ["h3"]
                                client = {"log": {"level": "error"}, "inbounds": [{"type": "mixed", "listen": "127.0.0.1", "listen_port": port}], "outbounds": [outbound], "route": {"final": "home"}}
                                write_json(folder / "client.json", client)
                                process = subprocess.Popen([str(find_core(ROOT, "sing-box")), "run", "-c", str(folder / "client.json")], stdout=logs, stderr=logs, cwd=folder)
                                try:
                                    wait_listen(process, port)
                                    self.assertIn(b"204", request(port))
                                finally:
                                    process.terminate()
                                    process.wait(timeout=5)
                    finally:
                        server.terminate()
                        server.wait(timeout=5)
        finally:
            http.shutdown()
            http.server_close()


if __name__ == "__main__":
    unittest.main()
