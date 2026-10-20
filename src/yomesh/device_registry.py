"""Per-installation device identities and SSH keys under ignored data/."""
from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import tempfile
import threading
from copy import deepcopy
from pathlib import Path

from .model import ConfigError


def _save_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".devices-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _host(value):
    if not isinstance(value, str) or not value or len(value) > 253 or not re.fullmatch(r"[A-Za-z0-9.:-]+", value):
        raise ConfigError("服务器地址需要 IP 或主机名，不包含协议和路径")
    if ":" in value:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            raise ConfigError("IPv6 地址无效") from None
    return value


def _absolute(value):
    if not isinstance(value, str) or not value.startswith('/') or '\x00' in value or '..' in Path(value).parts:
        raise ConfigError('请填写目标设备上的绝对路径')
    return value


class DeviceRegistry:
    def __init__(self, root: Path, transport=None):
        self.directory = Path(root) / "data"
        self.path = self.directory / "devices.json"
        self.keys = self.directory / "device-keys"
        self.lock = threading.RLock()
        if self.path.exists():
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get("devices"), list):
                raise ConfigError("设备目录无法读取，请恢复备份")
            self.state = value
        else:
            self.state = {"revision": 0, "devices": []}
        if transport is None:
            from .device_transport import SSHTransport
            transport = SSHTransport(self.keys)
        self.transport = transport

    def listing(self):
        with self.lock:
            return {"revision": self.state["revision"], "devices": [self._public(row) for row in self.state["devices"]]}

    @staticmethod
    def _public(row):
        return {key: row.get(key) for key in ("id", "name", "host", "port", "username", "host_key", "space_id", "last_read", "capabilities", "baseline_digest", "mihomo_home", "mihomo_config", "mihomo_binary", "localdns_root", "localdns_binary", "manager", "services")}

    def device(self, ident):
        with self.lock:
            row = next((item for item in self.state["devices"] if item["id"] == ident), None)
            if row is None:
                raise ConfigError("设备不存在")
            return row

    def _save(self):
        self.state["revision"] += 1
        try:
            _save_json(self.path, self.state)
        except Exception:
            self.state["revision"] -= 1
            raise

    def add(self, values, revision):
        import paramiko

        with self.lock:
            if revision != self.state["revision"]:
                raise ConfigError("设备目录已变化，请刷新后重试")
            if not isinstance(values, dict):
                raise ConfigError("服务器信息无效")
            name, host, username = values.get("name"), _host(values.get("host")), values.get("username", "root")
            port, host_key = values.get("port", 22), values.get("host_key")
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise ConfigError("服务器名称需要 1–80 个字符")
            if not isinstance(username, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
                raise ConfigError("SSH 用户名无效")
            if username != 'root':
                raise ConfigError('当前执行器需要受限的 root SSH 密钥；其他用户模式尚未验证')
            if type(port) is not int or not 1 <= port <= 65535:
                raise ConfigError("SSH 端口无效")
            if not isinstance(host_key, str) or not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", host_key):
                raise ConfigError("请填写经过核对的 SSH 主机指纹")
            paths = {key: _absolute(values.get(key)) for key in ("mihomo_home", "mihomo_config", "mihomo_binary", "localdns_root", "localdns_binary")}
            if paths["mihomo_home"].rstrip('/') in {'', '/etc', '/opt', '/usr', '/var', '/root', '/home'} or paths["localdns_root"].rstrip('/') in {'', '/etc', '/opt', '/usr', '/var', '/root', '/home'}:
                raise ConfigError('设备配置根目录范围过宽，请指定服务专用目录')
            if not paths["mihomo_config"].startswith(paths["mihomo_home"].rstrip('/') + '/'):
                raise ConfigError('Mihomo 主配置需位于其工作目录内')
            manager, services = values.get('manager'), values.get('services')
            if manager not in {'openrc', 'systemd'} or not isinstance(services, dict) or set(services) != {'mihomo', 'localdns'} or any(not isinstance(s, str) or not re.fullmatch(r'[A-Za-z0-9_.@-]{1,80}', s) for s in services.values()):
                raise ConfigError('请选择支持的服务管理方式和服务名称')
            if len(self.state["devices"]) >= 32 or any(d["host"] == host and d["port"] == port for d in self.state["devices"]):
                raise ConfigError("设备数量已达上限，或该服务器已经添加")
            ident = "device_" + secrets.token_hex(16)
            key = paramiko.RSAKey.generate(3072)
            self.keys.mkdir(parents=True, exist_ok=True)
            path = self.keys / (ident + ".pem")
            try:
                key.write_private_key_file(str(path))
                os.chmod(path, 0o600)
                row = {"id": ident, "name": name.strip(), "host": host, "port": port, "username": username,
                       "host_key": host_key, "space_id": None, "baseline": None, "baseline_digest": None, "refs": [],
                       "last_read": None, "capabilities": [], **paths, 'manager': manager, 'services': services}
                self.state["devices"].append(row)
                self._save()
            except Exception:
                self.state["devices"] = [item for item in self.state["devices"] if item.get("id") != ident]
                path.unlink(missing_ok=True)
                raise
            return {"device": self._public(row), "public_key": "ssh-rsa " + key.get_base64() + " yomesh-" + ident}

    def bind(self, ident, space_id, revision, spaces):
        with self.lock:
            row = self.device(ident)
            if revision != self.state["revision"]:
                raise ConfigError("设备目录已变化，请刷新后重试")
            if any(d.get("space_id") == space_id and d["id"] != ident for d in self.state["devices"]):
                raise ConfigError("该配置空间已绑定另一台服务器")
            profile = spaces.workspace_snapshot(space_id)["profile"]
            if "native_configuration" not in profile:
                raise ConfigError("请绑定原生 Mihomo / LocalDNS 配置空间")
            before = deepcopy(row)
            try:
                row["space_id"] = space_id
                row["baseline"] = None
                row["baseline_digest"] = None
                row["refs"] = []
                self._save()
            except Exception:
                row.clear()
                row.update(before)
                raise
            return self._public(row)

    def unbind(self, ident, revision):
        with self.lock:
            row = self.device(ident)
            if revision != self.state["revision"]:
                raise ConfigError("设备目录已变化，请刷新后重试")
            before = deepcopy(row)
            try:
                row.update(space_id=None, baseline=None, baseline_digest=None, refs=[])
                self._save()
            except Exception:
                row.clear()
                row.update(before)
                raise
            return self._public(row)

    def record_pull(self, ident, space_id, manifest, profile_digest, capabilities, refs):
        from datetime import UTC, datetime

        with self.lock:
            row = self.device(ident)
            before = deepcopy(row)
            try:
                row.update(space_id=space_id, baseline=manifest, baseline_digest=profile_digest,
                           last_read=datetime.now(UTC).isoformat(), capabilities=capabilities,
                           refs=list(refs))
                self._save()
            except Exception:
                row.clear()
                row.update(before)
                raise
            return self._public(row)
