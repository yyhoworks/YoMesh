"""Verify session authentication, password updates, and public endpoint exemptions."""
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.server import handler_for


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for name in ("examples", "web", "tools"):
            shutil.copytree(ROOT / name, self.root / name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), lambda *args, **kwargs: None)
        base_handler = handler_for(self.root, self.server.server_port)

        class QuietHandler(base_handler):
            def log_message(self, *args):
                pass

        self.server.RequestHandlerClass = QuietHandler
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.RequestHandlerClass.space_catalog.close()
        self.server.shutdown()
        self.server.server_close()
        self.temporary.cleanup()

    def request(self, path, value=None, headers=None):
        req = Request(
            f"http://127.0.0.1:{self.server.server_port}" + path,
            data=None if value is None else json.dumps(value).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urlopen(req, timeout=4) as resp:
                return resp.status, json.load(resp), dict(resp.headers.items())
        except HTTPError as error:
            return error.code, json.load(error), dict(error.headers.items())

    def test_auth_lifecycle_and_exemptions(self):
        # 1. Initially disabled: workspace and status are open
        code, status, _ = self.request("/api/auth/status")
        self.assertEqual(code, 200)
        self.assertFalse(status["enabled"])
        self.assertTrue(status["authenticated"])
        self.assertEqual(self.request("/api/workspace")[0], 200)

        # 2. Enable password protection via /api/auth/update
        code, updated, hdrs = self.request(
            "/api/auth/update",
            {"current_password": "", "username": "luyang", "new_password": "secret-password"},
        )
        self.assertEqual(code, 200, updated)
        self.assertTrue(updated["enabled"])
        self.assertEqual(updated["username"], "luyang")
        cookie = hdrs.get("Set-Cookie", "").split(";", 1)[0]
        self.assertTrue(cookie.startswith("yomesh_session="))

        # 3. Unauthenticated requests to /api/workspace and /api/spaces are blocked with 401
        code, err, _ = self.request("/api/workspace")
        self.assertEqual(code, 401)
        self.assertTrue(err.get("auth_required"))
        self.assertEqual(self.request("/api/spaces")[0], 401)

        # 4. /api/health remains accessible without authentication (for Docker healthcheck)
        code, health, _ = self.request("/api/health")
        self.assertEqual(code, 200)
        self.assertEqual(health["app"], "YoMesh")

        # 5. Authenticated requests with session cookie succeed
        code, ws, _ = self.request("/api/workspace", headers={"Cookie": cookie})
        self.assertEqual(code, 200)
        self.assertIn("profile", ws)

        # 6. Wrong login credentials fail with 422, right credentials issue new cookie
        code, bad_login, _ = self.request("/api/auth/login", {"username": "luyang", "password": "wrong"})
        self.assertEqual(code, 422)
        code, good_login, login_hdrs = self.request(
            "/api/auth/login", {"username": "luyang", "password": "secret-password"}
        )
        self.assertEqual(code, 200, good_login)
        cookie2 = login_hdrs.get("Set-Cookie", "").split(";", 1)[0]
        self.assertEqual(self.request("/api/workspace", headers={"Cookie": cookie2})[0], 200)

        # 7. Updating password requires current password and rotates session secret
        self.assertEqual(
            self.request(
                "/api/auth/update",
                {"current_password": "wrong", "username": "luyang", "new_password": "new-password"},
                headers={"Cookie": cookie2},
            )[0],
            422,
        )
        code, _, upd_hdrs = self.request(
            "/api/auth/update",
            {"current_password": "secret-password", "username": "luyang", "new_password": "new-password"},
            headers={"Cookie": cookie2},
        )
        self.assertEqual(code, 200)
        cookie3 = upd_hdrs.get("Set-Cookie", "").split(";", 1)[0]
        # Old cookie is invalidated because session_secret rotated
        self.assertEqual(self.request("/api/workspace", headers={"Cookie": cookie2})[0], 401)
        self.assertEqual(self.request("/api/workspace", headers={"Cookie": cookie3})[0], 200)

        # 8. Disabling password protection restores open access
        code, disabled, _ = self.request(
            "/api/auth/update",
            {"action": "disable", "current_password": "new-password"},
            headers={"Cookie": cookie3},
        )
        self.assertEqual(code, 200)
        self.assertFalse(disabled["enabled"])
        self.assertEqual(self.request("/api/workspace")[0], 200)


if __name__ == "__main__":
    unittest.main()
