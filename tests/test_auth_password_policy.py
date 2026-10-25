import base64
import secrets
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.auth import AuthStore, MIN_PASSWORD_LENGTH


class PasswordPolicyTests(unittest.TestCase):
    """短密码策略：长度不再限制，任意非空密码都可保存与验证。"""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.auth = AuthStore(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_min_password_length_allows_single_character(self):
        self.assertEqual(MIN_PASSWORD_LENGTH, 1)
        short = secrets.token_urlsafe(1)
        self.auth.update_credentials("", "luyang", short)
        self.auth.login("luyang", short)  # 不抛异常即验证通过

    def test_update_password_shortens_existing_credential(self):
        initial = secrets.token_urlsafe(12)
        self.auth.update_credentials("", "luyang", initial)
        shorter = secrets.token_urlsafe(1)
        self.auth.update_credentials(initial, "luyang", shorter)
        self.auth.login("luyang", shorter)


if __name__ == "__main__":
    unittest.main()
