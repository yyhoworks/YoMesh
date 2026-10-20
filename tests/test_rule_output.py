"""Native storage modes must preserve rules, dependencies and boolean routing."""
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import yaml
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import find_core, validate_bundle, write_bundle_files
from test_operations import profile as mixed_profile
from test_rule_management import fixture_profile, condition, rule
from test_local_dns import free_port, wait_listen


class RuleOutputTests(unittest.TestCase):
    def setUp(self):
        self.bodies = {
            "/mihomo": b'payload:\n  - IP-CIDR,127.0.0.0/8\n',
            "/sing-box": b'{"version":3,"rules":[{"ip_cidr":["127.0.0.0/8"]}]}',
        }
        self.seen = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append((self.path, self.headers.get("User-Agent")))
                self.send_response(200 if self.path in outer.bodies else 204)
                self.end_headers()
                if self.path in outer.bodies:
                    self.wfile.write(outer.bodies[self.path])
            def log_message(self, *args):
                pass
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()

    def test_local_files_load_for_both_cores_and_exclude_unused_rules(self):
        original = mixed_profile()
        before = deepcopy(original)
        for target in ("mihomo", "sing-box"):
            bundle = compile_bundle(original, target, rule_output="local")
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
            assets = bundle["manifest"]["rule_output"]["assets"]
            self.assertEqual({a["id"] for a in assets}, {"local", "remote", "home"})
            self.assertTrue(all(a["storage"] == "local" for a in assets))
            self.assertTrue(any(name.startswith("rules/mosdns_") for name in bundle["files"]))
            self.assertFalse(any("unused" in name or "disabled" in name for name in bundle["files"]))
            config = bundle["files"][target + (".yaml" if target == "mihomo" else ".json")]
            self.assertNotIn("local.example", json.dumps(config))
            self.assertNotIn("https://example.com/rules", json.dumps(config))
        self.assertEqual(original, before)

    def test_single_ip_matcher_and_multiple_values_have_native_parameter_order(self):
        for values in (("127.0.0.0/8",), ("127.0.0.0/8", "192.168.0.0/16")):
            value = fixture_profile()
            value["policies"] = [rule([{"operator": "or", "conditions": [condition("IP-CIDR", *values)]}])]
            bundle = compile_bundle(value)
            native = bundle["files"]["mihomo.yaml"]["rules"][0]
            self.assertTrue(native.endswith(",REJECT,no-resolve") if len(values) == 1 else native.endswith(")),REJECT"))
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")

    def test_incompatible_remote_formats_block_without_rewriting_source(self):
        value = fixture_profile()
        value["library"] = [{"id": "remote", "name": "远程规则", "kind": "ipcidr", "entries": ["127.0.0.0/8"], "source": {"type": "url", "url": f"http://127.0.0.1:{self.http.server_port}/mihomo", "format": "auto"}}]
        value["policies"] = [{"id": "legacy", "name": "legacy", "type": "RULE-SET", "value": "remote", "target": "DIRECT"}]
        before = deepcopy(value)
        with self.assertRaisesRegex(ConfigError, "不能直接供 sing-box"):
            compile_bundle(value, "sing-box", rule_output="remote")
        for mode in ("local", "inline"):
            self.assertEqual(validate_bundle(compile_bundle(value, "sing-box", rule_output=mode), ROOT)["status"], "passed")
        self.assertEqual(value, before)
        with self.assertRaisesRegex(ConfigError, "规则输出方式"):
            compile_bundle(value, rule_output="unknown")

    def test_remote_yaml_behavior_comes_from_original_payload(self):
        for payload, expected in ((["example.com", "+.example.net"], "domain"), (["DOMAIN,example.com"], "classical"), (["127.0.0.0/8"], "ipcidr")):
            self.bodies["/mihomo"] = yaml.safe_dump({"payload": payload}).encode()
            value = fixture_profile()
            value["library"] = [{"id": "remote", "name": "remote", "kind": "domain", "entries": ["snapshot.example"], "source": {"type": "url", "url": f"http://127.0.0.1:{self.http.server_port}/mihomo", "format": "clash"}}]
            value["policies"] = [{"id": "legacy", "name": "legacy", "type": "RULE-SET", "value": "remote", "target": "DIRECT"}]
            bundle = compile_bundle(value, rule_output="remote")
            self.assertEqual(bundle["files"]["mihomo.yaml"]["rule-providers"]["library_remote"]["behavior"], expected)
        self.bodies["/mihomo"] = b'payload:\n  - example.com\n  - IP-CIDR,127.0.0.0/8\n'
        with self.assertRaisesRegex(ConfigError, "混用"):
            compile_bundle(value, rule_output="remote")

    def test_local_and_remote_references_preserve_real_and_or_traffic(self):
        with tempfile.TemporaryDirectory() as generated:
            folder = Path(generated)
            source, binary = folder / "lan.txt", folder / "lan.mrs"
            source.write_text("127.0.0.0/8\n", encoding="utf-8")
            subprocess.run([str(find_core(ROOT, "mihomo")), "convert-ruleset", "ipcidr", "text", str(source), str(binary)], capture_output=True, check=True)
            self.bodies["/mihomo-binary"] = binary.read_bytes()
            source, binary = folder / "lan.json", folder / "lan.srs"
            source.write_bytes(self.bodies["/sing-box"])
            subprocess.run([str(find_core(ROOT, "sing-box")), "rule-set", "compile", str(source), "-o", str(binary)], capture_output=True, check=True)
            self.bodies["/sing-box-binary"] = binary.read_bytes()
        for target in ("mihomo", "sing-box"):
            for storage in ("local", "remote", "remote-binary"):
                mode = "remote" if storage == "remote-binary" else storage
                format_name = "auto" if storage == "remote-binary" else "clash" if target == "mihomo" else "sing-box"
                endpoint = target + ("-binary" if storage == "remote-binary" else "")
                for operator, blocked in (("and", False), ("or", True)):
                    with self.subTest(target=target, mode=storage, operator=operator), tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryFile() as logs:
                        value = fixture_profile()
                        value["library"] = [{"id": "lan", "name": "lan", "kind": "ipcidr", "entries": ["127.0.0.0/8"], "source": {"type": "url", "format": format_name, "url": f"http://127.0.0.1:{self.http.server_port}/{endpoint}", "user_agent": "YoMesh-rule-test", "interval_minutes": 60}}]
                        value["policies"] = [rule([{"operator": operator, "conditions": [condition("RULE-SET", "lan"), condition("IP-CIDR", "192.168.0.0/16")]}])]
                        readiness = rule([{"operator": "or", "conditions": [condition("RULE-SET", "lan")]}])
                        readiness.update(id="ready", name="ready", target="REJECT")
                        ready_port = free_port()
                        value.update(active_plan="active", routing_plans=[{"id": "active", "name": "active", "policies": deepcopy(value["policies"]), "fallback": "DIRECT"}, {"id": "ready", "name": "ready", "policies": [readiness], "fallback": "DIRECT"}], inbounds=[{"id": "ready", "name": "ready", "protocol": "mixed", "listen": "127.0.0.1", "port": ready_port, "enabled": True, "plan": "ready"}])
                        control_port = free_port()
                        value["routing_plans"].append({"id": "control", "name": "control", "policies": [], "fallback": "DIRECT"})
                        value["inbounds"].append({"id": "control", "name": "control", "protocol": "mixed", "listen": "127.0.0.1", "port": control_port, "enabled": True, "plan": "control"})
                        bundle = compile_bundle(value, target, rule_output=mode)
                        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
                        folder = Path(temporary)
                        write_bundle_files(folder, bundle["files"])
                        path = folder / (target + (".yaml" if target == "mihomo" else ".json"))
                        command = [str(find_core(ROOT, target)), "-d", temporary, "-f", str(path)] if target == "mihomo" else [str(find_core(ROOT, target)), "run", "-c", str(path)]
                        process = subprocess.Popen(command, cwd=folder, stdout=logs, stderr=logs)
                        try:
                            wait_listen(process, value["settings"]["mixed_port"])
                            def request(port, endpoint):
                                with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                                    client.sendall(f"GET http://127.0.0.1:{self.http.server_port}/{endpoint} HTTP/1.1\r\nHost: 127.0.0.1:{self.http.server_port}\r\nConnection: close\r\n\r\n".encode())
                                    response = b""
                                    try:
                                        while b"\r\n\r\n" not in response and len(response) < 4096:
                                            chunk = client.recv(4096)
                                            if not chunk:
                                                break
                                            response += chunk
                                    except ConnectionResetError:
                                        pass  # Native reject may reset the stream instead of returning HTTP.
                                    return response
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                try:
                                    if b"204" in request(control_port, "control") and b"204" not in request(ready_port, "ready"):
                                        break
                                except OSError:
                                    pass
                                time.sleep(.02)
                            else:
                                self.fail("rule provider never became ready")
                            before = len([p for p, _ in self.seen if p == "/policy"])
                            response = request(value["settings"]["mixed_port"], "policy")
                            if (b"204" in response) != (not blocked):
                                time.sleep(.1)
                                logs.seek(0)
                                self.fail(response.decode(errors="replace") + "\n" + logs.read().decode(errors="replace"))
                            self.assertEqual(len([p for p, _ in self.seen if p == "/policy"]) - before, 0 if blocked else 1)
                            if mode == "remote":
                                self.assertGreaterEqual(len([p for p, ua in self.seen if p == "/" + endpoint and ua == "YoMesh-rule-test"]), 2)
                        finally:
                            process.terminate()
                            process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
