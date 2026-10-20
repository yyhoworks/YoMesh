import io
import json
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError
from yomesh.native import find_core, validate_bundle, write_json
from yomesh.rule_sources import RuleLibraryService, parse_rules
from yomesh.workspace import Workspace
from test_local_dns import UDPFixture, free_port, wait_listen


def fixture_profile():
    return {"schema_version": 1, "sources": [], "nodes": [], "groups": [], "library": [], "policies": [], "fallback": "DIRECT", "settings": {"mixed_port": free_port(), "dns_port": free_port(), "dns_default": "local"}}


def condition(kind, *values):
    return {"type": kind, "value": values[0]} if kind == "RULE-SET" else {"type": kind, "values": list(values)}


def rule(groups, operator="or"):
    return {"id": "logical", "name": "组合条件", "type": "LOGICAL", "group_operator": operator, "condition_groups": groups, "target": "REJECT", "enabled": True}


class RuleManagement(unittest.TestCase):
    def test_native_boolean_rules_really_preserve_and_and_or(self):
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
        try:
            for target in ("mihomo", "sing-box"):
                for operator, blocked in (("and", False), ("or", True)):
                    with self.subTest(target=target, operator=operator), tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryFile() as logs:
                        profile = fixture_profile()
                        profile["library"] = [{"id": "lan", "name": "本机", "kind": "ipcidr", "entries": ["127.0.0.0/8"]}]
                        profile["policies"] = [rule([{"operator": operator, "conditions": [condition("RULE-SET", "lan"), condition("IP-CIDR", "192.168.0.0/16")]}])]
                        profile["routing_plans"] = [{"id": "active", "name": "active", "policies": deepcopy(profile["policies"]), "fallback": "DIRECT"}]
                        readiness = rule([{"operator": "or", "conditions": [condition("RULE-SET", "lan")]}])
                        readiness.update(id="readiness", name="readiness", target="REJECT")
                        profile["routing_plans"].append({"id": "ready", "name": "ready", "policies": [readiness], "fallback": "DIRECT"})
                        profile["active_plan"] = "active"
                        profile["inbounds"] = [{"id": "bound", "name": "bound", "protocol": "mixed", "listen": "127.0.0.1", "port": free_port(), "plan": "active", "enabled": True}]
                        ready_port = free_port()
                        profile["inbounds"].append({"id": "ready", "name": "ready", "protocol": "mixed", "listen": "127.0.0.1", "port": ready_port, "plan": "ready", "enabled": True})
                        control_port = free_port()
                        profile["routing_plans"].append({"id": "control", "name": "control", "policies": [], "fallback": "DIRECT"})
                        profile["inbounds"].append({"id": "control", "name": "control", "protocol": "mixed", "listen": "127.0.0.1", "port": control_port, "plan": "control", "enabled": True})
                        bundle = compile_bundle(profile, target)
                        self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
                        folder = Path(temporary)
                        filename = target + (".yaml" if target == "mihomo" else ".json")
                        write_json(folder / filename, bundle["files"][filename])
                        command = [str(find_core(ROOT, target)), "-d", temporary, "-f", str(folder / filename)] if target == "mihomo" else [str(find_core(ROOT, target)), "run", "-c", str(folder / filename)]
                        process = subprocess.Popen(command, cwd=folder, stdout=logs, stderr=logs)
                        try:
                            wait_listen(process, profile["settings"]["mixed_port"])
                            def request(port, endpoint):
                                with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                                    client.sendall(f"GET http://127.0.0.1:{http.server_port}/{endpoint} HTTP/1.1\r\nHost: 127.0.0.1:{http.server_port}\r\nConnection: close\r\n\r\n".encode())
                                    response = b""
                                    while b"\r\n\r\n" not in response and len(response) < 4096:
                                        try:
                                            chunk = client.recv(4096)
                                        except ConnectionResetError:
                                            break  # A native reject can close with TCP RST.
                                        if not chunk: break
                                        response += chunk
                                    return response
                            # A successful request through the same rule-set proves
                            # provider readiness; metadata ruleCount appears earlier.
                            deadline = time.monotonic() + 5
                            while time.monotonic() < deadline:
                                try:
                                    if b"204" in request(control_port, "control") and b"204" not in request(ready_port, "ready"):
                                        break
                                except OSError:
                                    pass
                                time.sleep(.01)
                            else:
                                self.fail("inline rule provider did not become usable")
                            before = seen.count("/policy")
                            response = request(profile["settings"]["mixed_port"], "policy")
                            if (b"204" in response) != (not blocked):
                                time.sleep(.1)  # Let the native logger flush failure diagnostics.
                                logs.seek(0)
                                self.fail(response.decode(errors="replace") + "\n" + logs.read().decode(errors="replace"))
                            self.assertEqual(seen.count("/policy") - before, 0 if blocked else 1)
                        finally:
                            process.terminate(); process.wait(timeout=5)
        finally:
            http.shutdown(); http.server_close()

    def test_mosdns_short_circuit_groups_match_actual_queries(self):
        with socketserver.ThreadingUDPServer(("127.0.0.1", 0), UDPFixture) as upstream, tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryFile() as logs:
            threading.Thread(target=upstream.serve_forever, daemon=True).start()
            profile = fixture_profile()
            profile["policies"] = [rule([{"operator": "and", "conditions": [condition("DOMAIN-SUFFIX", "example"), condition("DOMAIN-KEYWORD", "ads")]}, {"operator": "and", "conditions": [condition("DOMAIN-SUFFIX", "other"), condition("DOMAIN-KEYWORD", "deny")]}])]
            dns = compile_bundle(profile)["files"]["mosdns.yaml"]
            dns["plugins"][0]["args"]["upstreams"][0]["addr"] = f"127.0.0.1:{upstream.server_address[1]}"
            file = Path(temporary) / "mosdns.yaml"
            write_json(file, dns)
            process = subprocess.Popen([str(find_core(ROOT, "mosdns")), "start", "-c", str(file)], cwd=temporary, stdout=logs, stderr=logs)
            try:
                wait_listen(process, profile["settings"]["dns_port"])
                for host, expected in (("ads.example", 3), ("news.example", 0), ("deny.other", 3), ("ads.other", 0), ("news.none", 0)):
                    with self.subTest(host=host), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
                        query = b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00' + b''.join(bytes([len(p)]) + p.encode() for p in host.split('.')) + b'\x00\x00\x01\x00\x01'
                        client.settimeout(3); client.sendto(query, ("127.0.0.1", profile["settings"]["dns_port"]))
                        self.assertEqual(client.recv(4096)[3] & 15, expected)
            finally:
                process.terminate(); process.wait(timeout=5)
                upstream.shutdown()

    def test_rule_file_formats_and_unsupported_semantics(self):
        for content, format_name, count in ((b'payload:\n - DOMAIN-SUFFIX,example.com\n - IP-CIDR,192.168.0.0/16\n', "auto", 2), (b'||ads.example^\n! comment\n', "adguard", 1), (b'+.example.com\nexample.net\nfc00::/7\n', "auto", 3), (b'{"version":3,"rules":[{"domain_suffix":["example.com"]},{"ip_cidr":["192.168.0.0/16"]}]}', "sing-box", 2), (b'DOMAIN-REGEX,^ads[0-9]+\\.example$\n', "surge", 1)):
            with self.subTest(format_name=format_name):
                self.assertEqual(len(parse_rules(content, format_name, ROOT)[0]["entries"]), count)
        for content, format_name in ((b'@@||example.com^', "adguard"), (b'DOMAIN,example.com,DIRECT', "surge"), (b'{"version":3,"rules":[{"domain_suffix":["example.com"],"ip_cidr":["10.0.0.0/8"]}]}', "sing-box")):
            with self.assertRaises(ConfigError):
                parse_rules(content, format_name, ROOT)

    def test_real_mrs_and_srs_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            source, binary = folder / "domains.txt", folder / "domains.mrs"
            source.write_text("+.example.com\nexample.net\n", encoding="utf-8")
            subprocess.run([str(find_core(ROOT, "mihomo")), "convert-ruleset", "domain", "text", str(source), str(binary)], capture_output=True, check=True)
            parsed, detected = parse_rules(binary.read_bytes(), "auto", ROOT)
            self.assertEqual(detected, "mrs"); self.assertEqual(len(parsed["entries"]), 2)
            source, binary = folder / "domains.json", folder / "domains.srs"
            source.write_text('{"version":3,"rules":[{"domain_suffix":["example.com"]}]}', encoding="utf-8")
            subprocess.run([str(find_core(ROOT, "sing-box")), "rule-set", "compile", str(source), "-o", str(binary)], capture_output=True, check=True)
            parsed, detected = parse_rules(binary.read_bytes(), "auto", ROOT)
            self.assertEqual(detected, "srs"); self.assertEqual(parsed["entries"], ["DOMAIN-SUFFIX,example.com"])

    def test_rule_subscription_update_failure_retains_content_and_references(self):
        outer = self
        self.body = b'example.com\n'
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(outer.body)
            def log_message(self, *args):
                pass
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                workspace = Workspace(temporary)
                service = RuleLibraryService(ROOT, workspace)
                item, status = service.prepare({"name": "订阅", "source": {"type": "url", "url": f"http://127.0.0.1:{upstream.server_port}/rules", "auto_update": False}})
                state = workspace.save_library(item, status, 0)
                configuration = fixture_profile(); configuration["library"] = [item]
                configuration["policies"] = [rule([{"operator": "or", "conditions": [condition("RULE-SET", item["id"])]}])]
                workspace.save_profile(configuration, state["revision"])
                self.body = b'DOMAIN,example.org,PROXY\n'
                service.refresh(item["id"])
                deadline = time.monotonic() + 4
                while service.running and time.monotonic() < deadline: time.sleep(.01)
                state = workspace.snapshot()
                self.assertEqual(state["profile"]["library"][0]["entries"], ["DOMAIN,example.com"])
                self.assertEqual(state["library_status"][item["id"]]["refresh_state"], "error")
                with self.assertRaisesRegex(ConfigError, "引用"):
                    workspace.delete_library(item["id"], state["revision"])
                self.assertEqual(Workspace(temporary).snapshot()["profile"], state["profile"])
                renamed = deepcopy(item)
                renamed["name"] = "用户已修改"
                workspace.save_library(renamed, status, state["revision"])
                self.assertFalse(workspace.replace_library(item, status, expected=item))
                self.assertEqual(workspace.snapshot()["profile"]["library"][0]["name"], "用户已修改")
        finally:
            upstream.shutdown(); upstream.server_close()

    def test_mixed_ip_and_cannot_widen_dns_and_disabled_group_is_not_exported(self):
        profile = fixture_profile()
        profile["groups"] = [{"id": "disabled", "name": "停用草稿", "enabled": False, "mode": "select", "nodes": [], "sources": [], "groups": []}]
        profile["policies"] = [rule([{"operator": "and", "conditions": [condition("DOMAIN-SUFFIX", "example.com"), condition("IP-CIDR", "192.168.0.0/16")]}])]
        for target in ("mihomo", "sing-box"):
            bundle = compile_bundle(profile, target)
            dns = bundle["files"]["mosdns.yaml"]
            self.assertTrue(bundle["manifest"]["diagnostics"])
            self.assertFalse(any(p["type"] == "domain_set" for p in dns["plugins"]))
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
