"""Pinned SSH transport for the on-demand device executor."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
from pathlib import Path

from .model import ConfigError


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


class SSHTransport:
    """One request per SSH session; configuration travels on stdin, never argv."""

    def __init__(self, key_directory: Path):
        self.key_directory = Path(key_directory)

    def host_key(self, host: str, port: int):
        import paramiko

        sock = socket.create_connection((host, port), timeout=8)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=8)
            return fingerprint(transport.get_remote_server_key())
        except (OSError, paramiko.SSHException):
            raise ConfigError("无法读取目标服务器 SSH 身份") from None
        finally:
            transport.close()

    def call(self, device: dict, operation: str, payload: dict | None = None):
        import paramiko

        ident = device["id"]
        key_file = self.key_directory / (ident + ".pem")
        if not key_file.is_file():
            raise ConfigError("设备密钥不可用，请重新绑定服务器")

        expected = device["host_key"]

        class PinnedHostKey(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if not hmac.compare_digest(fingerprint(key), expected):
                    raise ConfigError("目标服务器 SSH 身份已变化，已停止连接")
                client.get_host_keys().add(hostname, key.get_name(), key)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedHostKey())
        try:
            key = paramiko.RSAKey.from_private_key_file(str(key_file))
            client.connect(device["host"], port=device["port"], username=device["username"],
                           pkey=key, look_for_keys=False, allow_agent=False, timeout=8,
                           auth_timeout=8, banner_timeout=8)
            stdin, stdout, _stderr = client.exec_command("yomesh-executor", timeout=45)
            request = json.dumps({"operation": operation, "payload": payload or {}}, ensure_ascii=False).encode()
            if len(request) > 24 * 1024 * 1024:
                raise ConfigError("设备任务超过 24 MiB")
            stdin.write(request)
            stdin.channel.shutdown_write()
            raw = stdout.read(32 * 1024 * 1024 + 1)
            if len(raw) > 32 * 1024 * 1024 or stdout.channel.recv_exit_status() != 0:
                raise ConfigError("设备执行器未完成请求，请检查连接和安装状态")
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("ok") is not True:
                raise ConfigError(str(result.get("error", "设备拒绝了请求")) if isinstance(result, dict) else "设备返回内容无效")
            return result["result"]
        except (OSError, EOFError, ValueError, paramiko.SSHException):
            raise ConfigError("无法连接设备执行器；确认 SSH、密钥和目标服务状态") from None
        finally:
            client.close()
