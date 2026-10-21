"""Session-based authentication for the YoMesh management workbench."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
import tempfile
from http.cookies import SimpleCookie
from pathlib import Path

from .model import ConfigError

SESSION_COOKIE = "yomesh_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 200_000
MIN_PASSWORD_LENGTH = 12


class AuthUnavailable(OSError):
    """Authentication storage failed; never interpret this as disabled auth."""


def _hash_password(password: str, salt_hex: str) -> str:
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt_hex),
        PBKDF2_ITERATIONS,
    )
    return dk.hex()


class AuthStore:
    """Manages persistent admin credentials and HMAC-signed session tokens."""

    def __init__(self, root: Path):
        self.root = root
        self.path = root / "data" / "auth.json"
        self._lock = threading.Lock()
        self._failed_attempts: list[float] = []
        self._configured = self.path.exists()
        self._ensure_bootstrap()

    def _ensure_bootstrap(self) -> None:
        if self.path.exists():
            return
        if os.environ.get("YOMESH_REQUIRE_AUTH") == "1":
            user = os.environ.get("YOMESH_INIT_USER", "admin").strip() or "admin"
            password = os.environ.get("YOMESH_INIT_PASSWORD", "")
            if len(password) < MIN_PASSWORD_LENGTH:
                raise ConfigError("启用登录必须显式设置 YOMESH_INIT_PASSWORD，至少 12 个字符；未创建默认账号")
            self._write_credentials(user, password, enabled=True)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._configured = True
            if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
                raise ValueError()
            if not isinstance(data.get("username"), str) or not 1 <= len(data["username"].strip()) <= 32:
                raise ValueError()
            if data["enabled"]:
                for field, length in (("salt", 32), ("password_hash", 64), ("session_secret", 64)):
                    if not isinstance(data.get(field), str) or not re.fullmatch(r"[0-9a-fA-F]{%d}" % length, data[field]):
                        raise ValueError()
            return data
        except FileNotFoundError:
            if not self._configured:
                return {"enabled": False, "username": "admin"}
        except (OSError, ValueError):
            pass
        raise AuthUnavailable("无法读取有效的登录凭据，访问已锁定；请管理员检查 data/auth.json 的权限或恢复有效备份") from None

    def _write_credentials(self, username: str, password: str, enabled: bool = True) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        salt = secrets.token_hex(16)
        secret = secrets.token_hex(32)
        payload = {
            "enabled": bool(enabled),
            "username": username,
            "salt": salt,
            "password_hash": _hash_password(password, salt),
            "session_secret": secret,
            "updated_at": int(time.time()),
        }
        self._save(payload)
        self._configured = True
        return payload

    def _save(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".auth-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def is_enabled(self) -> bool:
        with self._lock:
            data = self._read()
            return data["enabled"]

    def current_username(self) -> str:
        with self._lock:
            return str(self._read().get("username") or "admin")

    def _sign_token(self, username: str, secret: str, exp: int | None = None) -> str:
        expiry = exp if exp is not None else int(time.time()) + SESSION_TTL_SECONDS
        body = json.dumps({"u": username, "exp": expiry}, separators=(",", ":")).encode("utf-8")
        b64 = base64.urlsafe_b64encode(body).decode("ascii").rstrip("=")
        sig = hmac.new(secret.encode("ascii"), b64.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{b64}.{sig}"

    def verify_cookie(self, cookie_header: str | None) -> str | None:
        if not cookie_header:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(cookie_header)
        except Exception:
            return None
        morsel = cookie.get(SESSION_COOKIE)
        if not morsel or "." not in morsel.value:
            return None
        b64, sig = morsel.value.rsplit(".", 1)
        with self._lock:
            data = self._read()
            if not data.get("enabled"):
                return str(data.get("username") or "admin")
            secret = str(data.get("session_secret") or "")
            expected_user = str(data.get("username") or "")
        if not secret or not expected_user:
            return None
        expected_sig = hmac.new(secret.encode("ascii"), b64.encode("ascii"), hashlib.sha256).hexdigest()
        if not sig.isascii() or not hmac.compare_digest(sig, expected_sig):
            return None
        try:
            padded = b64 + "=" * (-len(b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict) or payload.get("u") != expected_user:
            return None
        if type(payload.get("exp")) is not int or payload["exp"] < int(time.time()):
            return None
        return expected_user

    def login(self, username: str, password: str) -> str:
        now = time.time()
        with self._lock:
            self._failed_attempts = [t for t in self._failed_attempts if now - t < 60]
            if len(self._failed_attempts) >= 10:
                raise ConfigError("登录尝试过于频繁，请等待 1 分钟后再试")
            data = self._read()
            if not data.get("enabled"):
                raise ConfigError("当前尚未启用密码登录")
            expected_user = str(data.get("username") or "")
            salt = str(data.get("salt") or "")
            expected_hash = str(data.get("password_hash") or "")
            secret = str(data.get("session_secret") or "")
            user_ok = hmac.compare_digest(username.strip().encode("utf-8"), expected_user.encode("utf-8"))
            pass_ok = bool(salt and expected_hash) and hmac.compare_digest(
                _hash_password(password, salt), expected_hash
            )
            if not (user_ok and pass_ok):
                self._failed_attempts.append(now)
                raise ConfigError("用户名或密码错误")
            self._failed_attempts.clear()
            return self._sign_token(expected_user, secret)

    def update_credentials(
        self,
        current_password: str,
        new_username: str,
        new_password: str,
    ) -> str:
        user = (new_username or "").strip()
        if not 1 <= len(user) <= 32:
            raise ConfigError("用户名长度需在 1 至 32 个字符之间")
        if not new_password or len(new_password) < MIN_PASSWORD_LENGTH:
            raise ConfigError("新密码长度至少需要 12 个字符")
        with self._lock:
            data = self._read()
            if data.get("enabled"):
                salt = str(data.get("salt") or "")
                expected_hash = str(data.get("password_hash") or "")
                if not current_password or not hmac.compare_digest(
                    _hash_password(current_password, salt), expected_hash
                ):
                    raise ConfigError("当前密码不正确")
            saved = self._write_credentials(user, new_password, enabled=True)
            return self._sign_token(user, saved["session_secret"])

    def disable_auth(self, current_password: str) -> None:
        with self._lock:
            data = self._read()
            if not data.get("enabled"):
                return
            salt = str(data.get("salt") or "")
            expected_hash = str(data.get("password_hash") or "")
            if not current_password or not hmac.compare_digest(
                _hash_password(current_password, salt), expected_hash
            ):
                raise ConfigError("当前密码不正确")
            data["enabled"] = False
            data["updated_at"] = int(time.time())
            self._save(data)
