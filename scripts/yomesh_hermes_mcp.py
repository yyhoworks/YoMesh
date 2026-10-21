#!/usr/bin/env python3
"""YoMesh MCP stdio bridge for Hermes Agent (Spaces, Rules, Nodes & Subscriptions)."""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

TOOLS = [
    {
        "name": "yomesh_list_spaces",
        "description": "List all YoMesh configuration spaces, node/group/rule counts, and subscription links.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "yomesh_diagnose_domain",
        "description": "Diagnose which routing rule and outbound group a domain or IP matches in YoMesh.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Domain name or IP address to diagnose, e.g. claude.ai or 1.1.1.1"},
                "space_id": {"type": "string", "description": "Optional space ID or name (defaults to active space)"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yomesh_add_rule",
        "description": "Add a routing rule (domain or IP-CIDR) to a target outbound group or DIRECT/REJECT in YoMesh.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry": {"type": "string", "description": "Rule matcher, e.g. DOMAIN-SUFFIX,openai.com or openai.com or 1.1.1.1/32"},
                "target": {"type": "string", "description": "Outbound group name, DIRECT, or REJECT"},
                "rule_name": {"type": "string", "description": "Optional rule group name"},
                "space_id": {"type": "string", "description": "Optional space ID or name"},
            },
            "required": ["entry", "target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yomesh_test_nodes",
        "description": "Inspect proxy nodes and latency status in YoMesh, optionally triggering a speed/latency test.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "test": {"type": "boolean", "description": "Set to true to trigger a latency test"},
                "space_id": {"type": "string", "description": "Optional space ID or name"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "yomesh_subscription_link",
        "description": "Get or generate a validated Mihomo (.yaml) or sing-box (.json) subscription link for LocalDNS or clients.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proxy": {"type": "string", "enum": ["mihomo", "sing-box"], "description": "Target proxy core"},
                "create": {"type": "boolean", "description": "Set to true to generate or refresh the subscription link"},
                "base_url": {"type": "string", "description": "Optional public/LAN base URL for the subscription link"},
                "space_id": {"type": "string", "description": "Optional space ID or name"},
            },
            "additionalProperties": False,
        },
    },
]


def _allowed_url(raw_url: str) -> str:
    url = str(raw_url or "").strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme == "https" and parsed.hostname:
        return url
    if parsed.scheme == "http" and parsed.hostname:
        if parsed.hostname == "localhost":
            return url
        try:
            ip = ipaddress.ip_address(parsed.hostname)
            if ip.is_loopback or ip.is_private:
                return url
        except ValueError:
            pass
    raise ValueError("YOMESH_URL 需使用 https:// 或局域网/本机 http:// 地址")


def load_bridge_config() -> tuple[str, str]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    args, _ = parser.parse_known_args()
    cfg = {}
    if args.config:
        cfg = json.loads(Path(args.config).expanduser().read_text(encoding="utf-8"))
    url = _allowed_url(cfg.get("url") or os.environ.get("YOMESH_URL") or "")
    token_file = cfg.get("token_file") or os.environ.get("YOMESH_HERMES_TOKEN_FILE")
    if token_file:
        token = Path(token_file).expanduser().read_text(encoding="utf-8").strip()
    else:
        token = str(cfg.get("token") or os.environ.get("YOMESH_HERMES_TOKEN") or "").strip()
    if not token.startswith("ymh_"):
        raise ValueError("缺少有效的 YoMesh Hermes 接入密钥")
    return url, token


def api_call(base_url: str, token: str, path: str, payload: dict | None = None) -> dict:
    parsed = urlsplit(base_url)
    host_header = parsed.netloc
    headers = {
        "Authorization": f"Bearer {token}",
        "Host": host_header,
        "Origin": f"{parsed.scheme}://{host_header}",
        "Content-Type": "application/json",
    }
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(f"{base_url}{path}", data=data, headers=headers, method="GET" if payload is None else "POST")
    try:
        with urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as err:
        try:
            detail = json.loads(err.read().decode("utf-8")).get("error") or str(err)
        except Exception:
            detail = str(err)
        raise RuntimeError(detail) from None
    except URLError as err:
        raise RuntimeError(f"无法连接 YoMesh ({base_url}): {err.reason}") from None


def dispatch_tool(base_url: str, token: str, name: str, arguments: dict) -> dict:
    if name == "yomesh_list_spaces":
        return api_call(base_url, token, "/api/hermes/spaces")
    if name == "yomesh_diagnose_domain":
        return api_call(base_url, token, "/api/hermes/diagnose", arguments)
    if name == "yomesh_add_rule":
        return api_call(base_url, token, "/api/hermes/rules/add", arguments)
    if name == "yomesh_test_nodes":
        return api_call(base_url, token, "/api/hermes/nodes", arguments)
    if name == "yomesh_subscription_link":
        return api_call(base_url, token, "/api/hermes/subscription", arguments)
    raise RuntimeError(f"未知的工具名称: {name}")


def main() -> int:
    try:
        base_url, token = load_bridge_config()
    except Exception as error:
        sys.stderr.write(f"YoMesh Hermes MCP 配置错误: {error}\n")
        return 2

    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg_id = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            reply = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "yomesh", "version": "0.17.1"},
                },
            }
        elif method in {"notifications/initialized", "initialized"}:
            continue
        elif method == "tools/list":
            reply = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            params = msg.get("params") or {}
            tname = params.get("name") or ""
            targs = params.get("arguments") or {}
            try:
                result = dispatch_tool(base_url, token, tname, targs)
                reply = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}],
                        "isError": False,
                    },
                }
            except Exception as error:
                reply = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(error)}],
                        "isError": True,
                    },
                }
        elif msg_id is not None:
            reply = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        else:
            continue
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
