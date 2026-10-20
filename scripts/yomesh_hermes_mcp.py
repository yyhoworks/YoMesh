"""Small stdio MCP bridge from Hermes to YoMesh's read-and-plan API.

The bridge has no SSH capability and cannot approve, apply or roll back a
release. Its token is read from a mode-0600 JSON file, never argv or env.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

IDENT = re.compile(r"device_[0-9a-f]{32}\Z")
RELEASE = re.compile(r"release_[0-9a-f]{32}\Z")
TOOLS = [
    {"name": "yomesh_list_devices", "description": "列出 YoMesh 已登记设备及绑定状态；不返回代理凭据。",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "yomesh_preview_device", "description": "只读比对指定设备的 Mihomo / LocalDNS 文件摘要与工作台空间，不修改设备。",
     "inputSchema": {"type": "object", "properties": {"device_id": {"type": "string"}},
                     "required": ["device_id"], "additionalProperties": False}},
    {"name": "yomesh_prepare_release", "description": "为指定设备预检并准备发布包；不会应用，必须在 YoMesh 工作台人工确认发布。",
     "inputSchema": {"type": "object", "properties": {"device_id": {"type": "string"}},
                     "required": ["device_id"], "additionalProperties": False}},
    {"name": "yomesh_list_releases", "description": "列出指定设备的发布记录与逐文件变更摘要。",
     "inputSchema": {"type": "object", "properties": {"device_id": {"type": "string"}},
                     "required": ["device_id"], "additionalProperties": False}},
    {"name": "yomesh_release_status", "description": "读取已准备发布的状态；不能确认或回滚。",
     "inputSchema": {"type": "object", "properties": {"release_id": {"type": "string"}},
                     "required": ["release_id"], "additionalProperties": False}},
]


def load_config(path: Path):
    info = path.stat()
    if os.name != "nt" and stat.S_IMODE(info.st_mode) & 0o077:
        raise ValueError("Hermes bridge 配置权限必须为 0600")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Hermes bridge 配置需要对象")
    base, token = value.get("base_url"), value.get("token")
    if not isinstance(base, str) or not isinstance(token, str) or not re.fullmatch(r"yomeshh_[A-Za-z0-9_-]{43}", token):
        raise ValueError("Hermes bridge 地址或凭据无效")
    parsed = urlparse(base)
    if (parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"})) or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Hermes bridge 需要 HTTPS 地址（仅本机测试可用 HTTP）")
    return base.rstrip("/"), token


def call_api(base, token, path, body=None):
    request = Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request, fp, code, msg, headers, newurl):
            return None
    try:
        with build_opener(NoRedirect).open(request, timeout=30) as response:
            return json.loads(response.read(2 * 1024 * 1024))
    except HTTPError as error:
        try:
            message = json.loads(error.read(4096)).get("error", "YoMesh 请求失败")
        except (ValueError, AttributeError):
            message = "YoMesh 请求失败"
        raise ValueError(str(message)[:300]) from None
    except URLError:
        raise ValueError("无法连接 YoMesh，请检查地址、TLS 和网络") from None


def run_tool(name, arguments, base, token):
    if not isinstance(arguments, dict):
        raise TypeError("工具参数需要对象")
    if name == "yomesh_list_devices":
        return call_api(base, token, "/api/hermes/devices")
    if name == "yomesh_release_status":
        ident = arguments.get("release_id")
        if not isinstance(ident, str) or not RELEASE.fullmatch(ident):
            raise ValueError("发布任务 ID 无效")
        return call_api(base, token, f"/api/hermes/releases/{ident}/status")
    ident = arguments.get("device_id")
    if not isinstance(ident, str) or not IDENT.fullmatch(ident):
        raise ValueError("设备 ID 无效")
    routes = {"yomesh_preview_device": ("preview", None),
              "yomesh_prepare_release": ("plan", {}),
              "yomesh_list_releases": ("releases", None)}
    if name not in routes:
        raise ValueError("工具不存在")
    action, body = routes[name]
    return call_api(base, token, f"/api/hermes/devices/{ident}/{action}", body)


def respond(ident, *, result=None, error=None):
    output = {"jsonrpc": "2.0", "id": ident}
    if error is not None:
        output["error"] = error
    else:
        output["result"] = result
    sys.stdout.write(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def serve(base, token):
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                continue
            ident = request.get("id")
            if ident is None:
                continue
            method = request.get("method")
            if method == "initialize":
                version = request.get("params", {}).get("protocolVersion", "2025-03-26")
                if version not in {"2025-03-26", "2025-06-18", "2026-07-28"}:
                    version = "2025-03-26"
                respond(ident, result={"protocolVersion": version, "capabilities": {"tools": {}},
                                       "serverInfo": {"name": "yomesh", "version": "0.16.3"}})
            elif method == "ping":
                respond(ident, result={})
            elif method == "tools/list":
                respond(ident, result={"tools": TOOLS})
            elif method == "tools/call":
                params = request.get("params", {})
                try:
                    value = run_tool(params.get("name"), params.get("arguments", {}), base, token)
                    respond(ident, result={"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]})
                except (ValueError, TypeError, OSError) as error:
                    respond(ident, result={"content": [{"type": "text", "text": str(error)[:300]}], "isError": True})
            else:
                respond(ident, error={"code": -32601, "message": "Method not found"})
        except (ValueError, TypeError, KeyError):
            # Do not write parse errors, request bodies or the token to stderr.
            continue


def main(argv=None):
    parser = argparse.ArgumentParser(description="YoMesh read-only Hermes MCP bridge")
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        base, token = load_config(args.config)
    except (OSError, ValueError, TypeError):
        print("YoMesh Hermes bridge 配置无效或不可读", file=sys.stderr)
        return 2
    serve(base, token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
