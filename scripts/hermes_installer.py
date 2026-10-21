#!/usr/bin/env python3
"""One-line auto-installer for YoMesh Hermes MCP on the Hermes host."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen


def _write_private(path: Path, content: bytes | str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8") if isinstance(content, str) else content
    path.write_bytes(data)
    os.chmod(path, mode)


def _update_hermes_yaml(config_path: Path, script_path: Path, bridge_json_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    block = (
        "  yomesh:\n"
        "    command: python3\n"
        "    args:\n"
        f"      - {script_path}\n"
        "      - --config\n"
        f"      - {bridge_json_path}\n"
        "    enabled: true\n"
    )
    if not config_path.exists():
        config_path.write_text("mcp_servers:\n" + block, encoding="utf-8")
        return

    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        doc = yaml.safe_load(text)
        if isinstance(doc, dict):
            servers = doc.setdefault("mcp_servers", {})
            if isinstance(servers, dict):
                servers["yomesh"] = {
                    "command": "python3",
                    "args": [str(script_path), "--config", str(bridge_json_path)],
                    "enabled": True,
                }
                config_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
                return
    except Exception:
        pass

    if "mcp_servers:" not in text:
        if not text.endswith("\n"):
            text += "\n"
        config_path.write_text(text + "\nmcp_servers:\n" + block, encoding="utf-8")
    elif "  yomesh:" not in text:
        idx = text.index("mcp_servers:") + len("mcp_servers:")
        rest = text[idx:]
        nl = rest.find("\n")
        insert_at = idx + (nl + 1 if nl >= 0 else len(rest))
        config_path.write_text(text[:insert_at] + block + text[insert_at:], encoding="utf-8")


def main() -> int:
    base_url = os.environ.get("YOMESH_URL", "").strip().rstrip("/")
    token = os.environ.get("YOMESH_TOKEN", "").strip()
    if not base_url or not token.startswith("ymh_"):
        sys.stderr.write("缺少 YOMESH_URL 或 YOMESH_TOKEN 环境变量，请从 YoMesh 工作台复制完整安装命令。\n")
        return 1

    home = Path.home()
    script_path = home / ".local/share/yomesh/yomesh_hermes_mcp.py"
    token_path = home / ".config/yomesh/hermes.token"
    bridge_json_path = home / ".config/yomesh/hermes.json"
    hermes_config = home / ".hermes/config.yaml"

    req = Request(f"{base_url}/api/setup/yomesh_hermes_mcp.py")
    with urlopen(req, timeout=15) as resp:
        mcp_code = resp.read()

    _write_private(script_path, mcp_code, 0o700)
    _write_private(token_path, token + "\n", 0o600)
    _write_private(
        bridge_json_path,
        json.dumps({"url": base_url, "token_file": str(token_path)}, ensure_ascii=False, indent=2) + "\n",
        0o600,
    )
    _update_hermes_yaml(hermes_config, script_path, bridge_json_path)

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
    raise SystemExit(main())
