"""Dedicated revocable bearer token store for Hermes MCP integration."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import threading

from .model import ConfigError


class HermesUnavailable(Exception):
    """Raised when the Hermes credential store cannot be read or written."""


class HermesAccess:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "data/hermes-access.json"
        self.lock = threading.RLock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "active": None}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise HermesUnavailable("Hermes 接入凭据存储无法读取，请检查 data/hermes-access.json") from error
        if not isinstance(data, dict) or data.get("version") != 1:
            raise HermesUnavailable("Hermes 接入凭据格式不受支持")
        return data

    def _save(self, data: dict) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_name(f".hermes-access.{secrets.token_hex(8)}.tmp")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(temp, flags, 0o600)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=2)
                    handle.write("\n")
                    handle.flush()
                    os.fsync(handle.fileno())
            except Exception:
                try:
                    temp.unlink()
                except OSError:
                    pass
                raise
            os.replace(temp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as error:
            raise HermesUnavailable("无法写入 Hermes 接入凭据，请检查 data 目录权限") from error

    def listing(self) -> dict:
        with self.lock:
            active = self._load().get("active")
            if not isinstance(active, dict) or not active.get("token_sha256"):
                return {"configured": False, "prefix": None, "created_at": None, "last_used_at": None}
            return {
                "configured": True,
                "prefix": active.get("prefix"),
                "created_at": active.get("created_at"),
                "last_used_at": active.get("last_used_at"),
            }

    def rotate(self) -> dict:
        with self.lock:
            token = "ymh_" + secrets.token_urlsafe(32)
            now = self._now()
            record = {
                "prefix": token[:10],
                "token_sha256": self._hash(token),
                "created_at": now,
                "last_used_at": None,
            }
            self._save({"version": 1, "active": record})
            return {
                "configured": True,
                "token": token,
                "prefix": record["prefix"],
                "created_at": now,
                "last_used_at": None,
            }

    def revoke(self) -> dict:
        with self.lock:
            self._save({"version": 1, "active": None})
            return {"configured": False, "prefix": None, "created_at": None, "last_used_at": None}

    def verify(self, header: str | None) -> bool:
        if not isinstance(header, str):
            return False
        scheme, _, raw = header.strip().partition(" ")
        token = raw.strip()
        if scheme.lower() != "bearer" or not token.startswith("ymh_") or len(token) < 20:
            return False
        with self.lock:
            data = self._load()
            active = data.get("active")
            if not isinstance(active, dict) or not isinstance(active.get("token_sha256"), str):
                return False
            if not hmac.compare_digest(active["token_sha256"], self._hash(token)):
                return False
            active["last_used_at"] = self._now()
            self._save(data)
            return True
