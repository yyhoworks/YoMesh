import base64
import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.model import ConfigError
from yomesh.subscriptions import parse_subscription, fetch_subscription
from yomesh.workspace import Workspace, Conflict, clean_source, next_update
from yomesh.services import SubscriptionService
from yomesh.compiler import compile_bundle
from yomesh.native import validate_bundle

UUID = "f509506e-85d5-43ca-9d92-2cd061548837"
URI = f"vless://{UUID}@127.0.0.1:22001?security=tls&sni=proxy.example.com&type=ws&path=%2Fws#HK-01"


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.source = clean_source({"id": "s1", "name": "test", "url": "https://example.com/sub"})

    def test_uri_base64_and_regex_filter(self):
        raw = URI + "\n" + URI.replace("22001", "22002").replace("HK-01", "traffic")
        self.source["exclude"] = "流量|(?i)traffic"
        nodes, stats = parse_subscription(base64.b64encode(raw.encode()), self.source)
        self.assertEqual((stats["retained"], stats["filtered"], stats["detected_format"]), (1, 1, "base64"))
        self.assertEqual(nodes[0]["transport"]["path"], "/ws")

    def test_clash_and_singbox_formats(self):
        clash = 'proxies:\n  - {name: SG, type: ss, server: 127.0.0.1, port: 22001, cipher: aes-128-gcm, password: test}\n'
        nodes, stats = parse_subscription(clash, self.source)
        self.assertEqual(nodes[0]["protocol"], "shadowsocks")
        sing = {"outbounds": [{"tag": "manual", "type": "selector", "outbounds": ["test"]}, {"tag": "test", "type": "trojan", "server": "127.0.0.1", "server_port": 22002, "password": "test", "tls": {"enabled": True, "server_name": "test.example"}}]}
        other, _ = parse_subscription(json.dumps(sing), self.source)
        self.assertEqual(other[0]["protocol"], "trojan")

    def test_protocol_filter_and_stable_ids(self):
        first, _ = parse_subscription(URI, self.source)
        second, _ = parse_subscription(URI.replace("HK-01", "renamed"), self.source)
        self.assertEqual(first[0]["id"], second[0]["id"])
        self.source["disabled_protocols"] = ["vless"]
        nodes, stats = parse_subscription(URI, self.source)
        self.assertEqual((nodes, stats["filtered"]), ([], 1))

    def test_shared_endpoint_accounts_remain_distinct(self):
        nodes, stats = parse_subscription("ss://aes-128-gcm:first@127.0.0.1:22001#one\nss://aes-128-gcm:second@127.0.0.1:22001#two", self.source)
        self.assertEqual((len(nodes), stats["filtered"]), (2, 0))

    def test_ss2022_native_export(self):
        for method, length in (("2022-blake3-aes-128-gcm", 16), ("2022-blake3-aes-256-gcm", 32), ("2022-blake3-chacha20-poly1305", 32)):
            key = base64.b64encode(bytes(range(length))).decode()
            nodes, _ = parse_subscription(f"ss://{method}:{key}@127.0.0.1:22001#fixture", self.source)
            profile = {"schema_version": 1, "sources": [self.source], "nodes": nodes, "fallback": nodes[0]["id"]}
            for target in ("mihomo", "sing-box"):
                with self.subTest(method=method, target=target):
                    self.assertEqual(validate_bundle(compile_bundle(profile, target), ROOT)["status"], "passed")

    def test_surges_ss_and_vmess_uri(self):
        self.source["format"] = "surge"
        nodes, _ = parse_subscription('[Proxy]\nTest = ss, 127.0.0.1, 22001, encrypt-method=aes-128-gcm, password=test', self.source)
        self.assertEqual(nodes[0]["method"], "aes-128-gcm")
        self.source["format"] = "uri"
        uri = "vmess://" + base64.b64encode(json.dumps({"add": "127.0.0.1", "port": "22002", "id": UUID, "aid": "0", "net": "tcp", "ps": "JP"}).encode()).decode()
        nodes, _ = parse_subscription(uri, self.source)
        self.assertEqual(nodes[0]["protocol"], "vmess")

    def test_native_export_after_subscription_import(self):
        nodes, _ = parse_subscription(URI, self.source)
        profile = {"schema_version": 1, "sources": [self.source], "nodes": nodes, "fallback": nodes[0]["id"]}
        for target in ("mihomo", "sing-box"):
            with self.subTest(target=target):
                self.assertEqual(validate_bundle(compile_bundle(profile, target), ROOT)["status"], "passed")

    def test_atomic_save_restart_and_failure_preserves_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Workspace(directory)
            sid = store.save_source(self.source, revision=0)
            source = store.snapshot()["profile"]["sources"][0]
            nodes, stats = parse_subscription(URI, source)
            store.replace_nodes(source, nodes, stats)
            reopened = Workspace(directory)
            self.assertEqual(len(reopened.snapshot()["profile"]["nodes"]), 1)
            def broken(source):
                raise ConfigError("test upstream unavailable")
            service = SubscriptionService(ROOT, reopened, broken)
            service.refresh(sid)
            deadline = time.monotonic() + 3
            while service.busy(sid) and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertEqual(len(reopened.snapshot()["profile"]["nodes"]), 1)
            self.assertEqual(reopened.snapshot()["source_status"][sid]["refresh_state"], "error")
            with self.assertRaises(Conflict):
                reopened.save_source(source, sid, revision=0)

    def test_local_fetch_user_agent_and_traffic(self):
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.headers.get("User-Agent"))
                self.send_response(200)
                self.send_header("Subscription-Userinfo", "upload=1; download=2; total=100; expire=1900000000")
                self.end_headers()
                self.wfile.write(URI.encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        Thread(target=server.serve_forever, daemon=True).start()
        try:
            self.source.update(url=f"http://127.0.0.1:{server.server_port}/sub", user_agent="YoMesh-test")
            nodes, stats = fetch_subscription(self.source)
            self.assertEqual((len(nodes), seen, stats["traffic"]["download"]), (1, ["YoMesh-test"], 2))
        finally:
            server.shutdown()
            server.server_close()

    def test_delete_cleans_members_and_daily_schedule(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Workspace(directory)
            sid = store.save_source(self.source, revision=0)
            store.delete_source(sid, store.snapshot()["revision"])
            self.assertEqual(store.snapshot()["profile"]["groups"][0]["sources"], [])
        now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc).timestamp()
        self.source.update(schedule_mode="daily", daily_time="04:00")
        target = next_update(self.source, now)
        self.assertEqual(datetime.fromtimestamp(target, timezone.utc).isoformat(), "2026-10-18T20:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
