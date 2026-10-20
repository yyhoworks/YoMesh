"""Hermes receives a revocable plan-only credential, never admin authority."""
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

from yomesh.hermes_access import HermesAccess, HermesUnavailable
from yomesh.server import handler_for


class HermesAccessTests(unittest.TestCase):
    def test_token_is_hashed_rotated_revoked_and_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            access = HermesAccess(Path(folder))
            self.assertFalse(access.listing()["enabled"])
            first = access.rotate()["token"]
            self.assertTrue(access.verify("Bearer " + first))
            self.assertNotIn(first, access.path.read_text(encoding="utf-8"))
            second = access.rotate()["token"]
            self.assertFalse(access.verify("Bearer " + first))
            self.assertTrue(access.verify("Bearer " + second))
            self.assertFalse(access.revoke()["enabled"])
            self.assertFalse(access.verify("Bearer " + second))
            access.path.write_text("{", encoding="utf-8")
            with self.assertRaises(HermesUnavailable):
                access.verify("Bearer " + second)
            with self.assertRaises(HermesUnavailable):
                access.rotate()

    def test_hermes_token_cannot_call_admin_or_publish_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(root, 8767))
            handler = server.RequestHandlerClass
            handler.log_message = lambda *args: None
            handler.auth_store.update_credentials("", "admin", "fixture-password")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(method, path, body=None, *, cookie=None, bearer=None):
                conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                headers = {"Host": "127.0.0.1:8767", "Content-Type": "application/json"}
                if cookie:
                    headers["Cookie"] = cookie
                if bearer:
                    headers["Authorization"] = "Bearer " + bearer
                conn.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
                response = conn.getresponse()
                result = response.status, dict(response.getheaders()), json.loads(response.read())
                conn.close()
                return result

            try:
                self.assertEqual(request("GET", "/api/hermes/devices")[0], 401)
                code, headers, _ = request("POST", "/api/auth/login", {"username": "admin", "password": "fixture-password"})
                self.assertEqual(code, 200)
                cookie = headers["Set-Cookie"].split(";", 1)[0]
                code, _, rotated = request("POST", "/api/integrations/hermes/rotate", {}, cookie=cookie)
                self.assertEqual(code, 200)
                token = rotated["token"]
                self.assertEqual(request("GET", "/api/hermes/devices", bearer=token)[0], 200)
                self.assertEqual(request("GET", "/api/devices", bearer=token)[0], 401)
                self.assertEqual(request("POST", "/api/devices/releases/release_" + "a" * 32 + "/approve", {}, bearer=token)[0], 401)
                self.assertEqual(request("POST", "/api/integrations/hermes/revoke", {}, cookie=cookie)[0], 200)
                self.assertEqual(request("GET", "/api/hermes/devices", bearer=token)[0], 401)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                handler.space_catalog.close()
