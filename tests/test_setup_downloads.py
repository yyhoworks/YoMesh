"""Installation downloads contain only the selected shipped script."""
import json
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.server import handler_for


class SetupDownloadTests(unittest.TestCase):
    def test_downloads_require_login_and_allow_only_shipped_setup_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts").mkdir()
            names = ("device_installer.py", "device_executor.py", "yomesh_hermes_mcp.py")
            for name in names:
                (root / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(root, 8767))
            handler = server.RequestHandlerClass
            handler.log_message = lambda *args: None
            handler.auth_store.update_credentials("", "fixture", "fixture-password")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(path, body=None, cookie=None, bearer=None):
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                headers = {"Host": "127.0.0.1:8767", "Content-Type": "application/json"}
                if cookie:
                    headers["Cookie"] = cookie
                if bearer:
                    headers["Authorization"] = "Bearer " + bearer
                try:
                    conn.request("POST" if body else "GET", path,
                                 json.dumps(body).encode() if body else None, headers)
                    response = conn.getresponse()
                    return response.status, dict(response.getheaders()), response.read()
                finally:
                    conn.close()

            try:
                self.assertEqual(request("/api/setup/device_installer.py")[0], 401)
                token = handler.hermes_store.rotate()["token"]
                self.assertEqual(request("/api/setup/device_installer.py", bearer=token)[0], 401)
                status, headers, _ = request("/api/auth/login", {"username": "fixture", "password": "fixture-password"})
                self.assertEqual(status, 200)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                for name in names:
                    status, headers, body = request("/api/setup/" + name, cookie=cookie)
                    self.assertEqual(status, 200)
                    self.assertEqual(body, (ROOT / "scripts" / name).read_bytes())
                    self.assertEqual(headers["Content-Disposition"], f'attachment; filename="{name}"')
                for name in ("../data/auth.json", "%2e%2e/data/auth.json", "yomesh.py", "device_installer.py?other=1"):
                    self.assertEqual(request("/api/setup/" + name, cookie=cookie)[0], 404)
                (root / "scripts" / names[0]).unlink()
                self.assertEqual(request("/api/setup/" + names[0], cookie=cookie)[0], 503)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                handler.space_catalog.close()
