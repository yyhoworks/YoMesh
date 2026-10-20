"""Prove latency uses the real proxy path with a local encrypted SS fixture."""
import json
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.native import find_core, write_json
from yomesh.workspace import Workspace
from yomesh.services import SubscriptionService
from yomesh.subscriptions import parse_subscription
from test_local_dns import free_port, wait_listen


class LatencyTests(unittest.TestCase):
    def test_real_http_latency_over_encrypted_proxy(self):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(204)
                self.end_headers()
            def do_HEAD(self):
                self.do_GET()
            def log_message(self, *args):
                pass
        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=http.serve_forever, daemon=True).start()
        with tempfile.TemporaryDirectory() as temporary, tempfile.TemporaryFile() as logs:
            root = Path(temporary)
            port = free_port()
            configuration = {"log": {"level": "error"}, "inbounds": [{"type": "shadowsocks", "listen": "127.0.0.1", "listen_port": port, "method": "aes-128-gcm", "password": "fixture"}], "outbounds": [{"type": "direct", "tag": "direct"}], "route": {"final": "direct"}}
            write_json(root / "server.json", configuration)
            process = subprocess.Popen([str(find_core(ROOT, "sing-box")), "run", "-c", str(root / "server.json")], stdout=logs, stderr=logs)
            try:
                wait_listen(process, port)
                workspace = Workspace(root / "data")
                ident = workspace.save_source({"name": "local latency", "url": "https://example.com/fixture", "test_url": f"http://127.0.0.1:{http.server_port}/generate_204", "test_timeout_ms": 3000}, revision=0)
                source = workspace.snapshot()["profile"]["sources"][0]
                nodes, stats = parse_subscription(f"ss://aes-128-gcm:fixture@127.0.0.1:{port}#local", source)
                workspace.replace_nodes(source, nodes, stats)
                service = SubscriptionService(ROOT, workspace)
                service.test(ident)
                deadline = time.monotonic() + 10
                while service.busy(ident) and time.monotonic() < deadline:
                    time.sleep(.03)
                state = workspace.snapshot()
                self.assertEqual(state["source_status"][ident]["test_state"], "success")
                self.assertEqual(state["node_status"][nodes[0]["id"]]["state"], "success")
                self.assertIn("/generate_204", requests)
            finally:
                process.terminate()
                process.wait(timeout=5)
                http.shutdown()
                http.server_close()


if __name__ == '__main__':
    unittest.main()
