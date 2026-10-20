"""Revocable, read-and-plan-only credential for a user's Hermes MCP bridge."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import threading
import time
from pathlib import Path


class HermesUnavailable(OSError):
    """Credential storage failed; never treat it as a missing integration."""


TOKEN_PATTERN = re.compile(r"yomeshh_[A-Za-z0-9_-]{43}")


class HermesAccess:
    def __init__(self, root: Path):
        self.path = Path(root) / "data" / "hermes-access.json"
        self.lock = threading.RLock()

    def _read(self):
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise HermesUnavailable("Hermes 凭据无法读取，接入已锁定；请恢复备份") from None
        if (not isinstance(value, dict) or value.get("format") != "yomesh-hermes-v1" or
                not isinstance(value.get("token_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", value["token_sha256"]) or
                type(value.get("created")) is not int):
            raise HermesUnavailable("Hermes 凭据结构无效，接入已锁定；请恢复备份")
        return value

    def _write(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".hermes-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def listing(self):
        with self.lock:
            value = self._read()
            active = bool(value and value["token_sha256"] != "0" * 64)
            return {"enabled": active, "created": value["created"] if active else None,
                    "scope": "read-and-prepare-only"}

    def rotate(self):
        with self.lock:
            # A corrupt credential must be restored, not silently replaced.
            self._read()
            token = "yomeshh_" + secrets.token_urlsafe(32)
            value = {"format": "yomesh-hermes-v1", "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
                     "created": int(time.time())}
            self._write(value)
            return {"token": token, **self.listing()}

    def revoke(self):
        with self.lock:
            self._read()
            if self.path.exists():
                # Replace with a valid disabled record rather than deleting a
                # credential file and accidentally re-entering bootstrap mode.
                self._write({"format": "yomesh-hermes-v1", "token_sha256": "0" * 64,
                             "created": int(time.time())})
            return {"enabled": False, "created": None, "scope": "read-and-prepare-only"}

    def verify(self, authorization: str | None):
        if not isinstance(authorization, str) or not authorization.startswith("Bearer "):
            return False
        token = authorization.removeprefix("Bearer ")
        if not TOKEN_PATTERN.fullmatch(token):
            return False
        with self.lock:
            value = self._read()
            return bool(value and value["token_sha256"] != "0" * 64 and
                        hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), value["token_sha256"]))
