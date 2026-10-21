#!/usr/bin/env python3
"""One-line auto-installer for YoMesh Hermes MCP on the Hermes host."""
from __future__ import annotations

import json
import hashlib
import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def _write_private(path: Path, content: bytes | str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("安装目标是符号链接，请先核对目标文件")
    data = content.encode("utf-8") if isinstance(content, str) else content
    descriptor, temporary = tempfile.mkstemp(prefix=".yomesh-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.chmod(temporary, mode)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _hermes_yaml(config_path: Path, script_path: Path, bridge_json_path: Path) -> str:
    if config_path.is_symlink():
        raise ValueError("Hermes 配置是符号链接，请先核对目标文件")
    doc = {}
    yaml_module = None
    if config_path.exists():
        try:
            import yaml  # type: ignore
        except ImportError:
            raise ValueError("读取已有 Hermes YAML 需要 PyYAML；请安装后重试，原配置未修改") from None
        yaml_module = yaml
        try:
            doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            raise ValueError("Hermes YAML 无法解析，原配置未修改") from None
        if not isinstance(doc, dict):
            raise ValueError("Hermes 配置需要 YAML 对象，原配置未修改")
    servers = doc.setdefault("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("Hermes mcp_servers 需要对象，原配置未修改")
    servers["yomesh"] = {"command": "python3", "args": [str(script_path), "--config", str(bridge_json_path)], "enabled": True}
    if yaml_module is not None:
        return yaml_module.safe_dump(doc, allow_unicode=True, sort_keys=False)
    # JSON is a YAML subset and avoids string insertion when PyYAML is absent.
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _allowed_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        parsed.port
        if parsed.username is not None or parsed.password is not None or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError()
        if parsed.scheme == "https":
            return value
        if parsed.scheme == "http":
            if parsed.hostname == "localhost":
                return value
            address = ipaddress.ip_address(parsed.hostname)
            if address.is_loopback or address.is_private:
                return value
    except ValueError:
        pass
    raise ValueError("YOMESH_URL 需要 HTTPS 或本机/私网 HTTP 地址，不包含路径、凭据和查询参数")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main() -> int:
    base_url = _allowed_url(os.environ.get("YOMESH_URL", ""))
    token = os.environ.get("YOMESH_TOKEN", "").strip()
    digest = os.environ.get("YOMESH_MCP_SHA256", "").strip()
    if not token.startswith("ymh_") or not re.fullmatch(r"[0-9a-f]{64}", digest):
        sys.stderr.write("缺少接入密钥或脚本校验值，请从可信 YoMesh 工作台重新复制完整安装命令。\n")
        return 1

    home = Path.home()
    script_path = home / ".local/share/yomesh/yomesh_hermes_mcp.py"
    token_path = home / ".config/yomesh/hermes.token"
    bridge_json_path = home / ".config/yomesh/hermes.json"
    hermes_config = home / ".hermes/config.yaml"
    config_text = _hermes_yaml(hermes_config, script_path, bridge_json_path)

    req = Request(f"{base_url}/api/setup/yomesh_hermes_mcp.py")
    with build_opener(_NoRedirect()).open(req, timeout=15) as resp:
        mcp_code = resp.read(512 * 1024 + 1)
    if len(mcp_code) > 512 * 1024 or hashlib.sha256(mcp_code).hexdigest() != digest:
        raise ValueError("MCP 脚本 SHA-256 校验失败，未修改安装文件；请从可信工作台重新复制命令")

    _write_private(script_path, mcp_code, 0o700)
    _write_private(token_path, token + "\n", 0o600)
    _write_private(
        bridge_json_path,
        json.dumps({"url": base_url, "token_file": str(token_path)}, ensure_ascii=False, indent=2) + "\n",
        0o600,
    )
    _write_private(hermes_config, config_text)

    print(f"[1/2] 已写入桥接脚本：{script_path}")
    print(f"[2/2] 已更新 Hermes 配置：{hermes_config}")

    hermes_bin = shutil.which("hermes") or (str(home / ".local/bin/hermes") if (home / ".local/bin/hermes").exists() else None)
    if hermes_bin:
        print("正在运行 hermes mcp test yomesh 验证连接...")
        subprocess.run([hermes_bin, "mcp", "test", "yomesh"], check=False)
    else:
        print("YoMesh Hermes MCP 已配置完成！现在可以在 Hermes 中直接管理节点、分流规则与订阅链接。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        sys.stderr.write(str(error) + "\n")
        raise SystemExit(1) from None
    except OSError:
        sys.stderr.write("安装未完成：请检查地址、脚本 SHA-256、Hermes YAML 与目录权限后重试；未执行未通过校验的脚本。\n")
        raise SystemExit(1) from None
