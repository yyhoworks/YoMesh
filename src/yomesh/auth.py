"""Session-based authentication for the YoMesh management workbench."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie
from pathlib import Path

from .model import ConfigError

SESSION_COOKIE = "yomesh_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 200_000


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
        self._ensure_bootstrap()

    def _ensure_bootstrap(self) -> None:
        if self.path.exists():
            return
        if os.environ.get("YOMESH_REQUIRE_AUTH") == "1":
            user = os.environ.get("YOMESH_INIT_USER", "admin").strip() or "admin"
            password = os.environ.get("YOMESH_INIT_PASSWORD", "admin123")
            self._write_credentials(user, password, enabled=True)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"enabled": False, "username": "admin"}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {"enabled": False, "username": "admin"}
            return data
        except (OSError, ValueError):
            return {"enabled": False, "username": "admin"}

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
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)
        return payload

    def is_enabled(self) -> bool:
        with self._lock:
            data = self._read()
            return bool(data.get("enabled") and data.get("password_hash") and data.get("session_secret"))

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
        if not hmac.compare_digest(sig, expected_sig):
            return None
        try:
            padded = b64 + "=" * (-len(b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return None
        if payload.get("u") != expected_user:
            return None
        if int(payload.get("exp") or 0) < int(time.time()):
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
            user_ok = hmac.compare_digest(username.strip(), expected_user)
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
        if not new_password or len(new_password) < 4:
            raise ConfigError("新密码长度至少需要 4 个字符")
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
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
