"""Safe, bounded native probe results without raw core errors or credentials."""
import json
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit


def _failure(error):
    message = ""
    if isinstance(error, HTTPError):
        try:
            message = str(json.loads(error.read(4096)).get("message", "")).lower()
        except (ValueError, OSError, AttributeError):
            pass
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, (TimeoutError, socket.timeout)) or any(t in message for t in ("timeout", "timed out", "deadline exceeded")):
        return "timeout", "代理请求超时", "可重试，或换一个测试目标确认目标站可达性。"
    if isinstance(reason, socket.gaierror) or any(t in message for t in ("no such host", "name resolution", "dns lookup")):
        return "dns", "节点地址解析失败", "检查节点地址与群晖使用的 DNS。"
    if isinstance(reason, ConnectionRefusedError) or "connection refused" in message:
        return "refused", "连接被拒绝", "检查节点端口及服务是否运行。"
    if any(t in message for t in ("certificate", "tls handshake", "x509")):
        return "tls", "TLS 握手或证书检查失败", "核对节点证书、服务器名称和设备时间。"
    return "proxy_request", "代理请求未成功，内核未提供具体原因", "重试并核对节点参数，或在 Bettbox 中测试同一节点。"


def _transport_check(node):
    # A TCP check says nothing about UDP-based or chained transports.
    if node.get("type") in {"hysteria", "hysteria2", "tuic", "wireguard"}:
        return {"transport": "not_checked_udp", "transport_note": "此协议使用 UDP，未用 TCP 探测代替代理测试。"}
    if any(node.get(k) for k in ("dialer-proxy", "interface-name", "routing-mark")):
        return {"transport": "not_checked_chain", "transport_note": "节点存在链式或指定出口，未做直连 TCP 推断。"}
    try:
        with socket.create_connection((node["server"], int(node["port"])), timeout=2):
            return {"transport": "tcp_reachable", "transport_note": "节点 TCP 端口可达；尚不能区分代理握手、认证和目标站失败。"}
    except socket.gaierror:
        return {"transport": "dns_failed", "transport_note": "群晖无法解析节点地址。"}
    except ConnectionRefusedError:
        return {"transport": "tcp_refused", "transport_note": "节点 TCP 连接被拒绝。"}
    except (TimeoutError, socket.timeout):
        return {"transport": "tcp_timeout", "transport_note": "节点 TCP 连接超时。"}
    except (OSError, ValueError, KeyError, TypeError):
        return {"transport": "unconfirmed", "transport_note": "未能确认节点 TCP 可达性。"}


def measure_node(core, row, url, timeout):
    started = time.monotonic()
    try:
        value = core("/proxies/probe_" + row["id"] + "/delay?" + urlencode({"url": url, "timeout": timeout}), timeout / 1000 + 3).get("delay")
        if type(value) is not int or value < 0:
            raise ValueError("invalid delay")
        result = {"state": "success", "latency_ms": value}
    except Exception as error:
        code, label, hint = _failure(error)
        result = {"state": "failed", "latency_ms": None, "error_code": code, "error": label, "hint": hint}
        if code == "proxy_request":
            result.update(_transport_check(row["raw"]))
    target = urlsplit(url)
    return {**result, "tested_at": time.time(), "duration_ms": round((time.monotonic() - started) * 1000),
            "location": "YoMesh 所在设备", "test_target": target.hostname, "timeout_ms": timeout}


def record_probe_result(workspace, ident, result, valid_ids):
    """Fourteen measurements per current node; reuse legacy latest result once."""
    with workspace.lock:
        statuses = workspace.state.setdefault("node_status", {})
        for key in list(statuses):
            if key not in valid_ids:
                statuses.pop(key)
        previous = statuses.get(ident, {})
        history = previous.get("history", [])
        if not history and previous.get("tested_at"):
            history = [{k: v for k, v in previous.items() if k != "history"}]
        statuses[ident] = {**result, "history": [dict(result), *history[:13]]}
