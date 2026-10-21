"""Parse subscription snapshots into the shared node model, with visible skips."""
from __future__ import annotations

import base64
import csv
import hashlib
import gzip
import io
import json
import re
from urllib.parse import parse_qs, unquote, urlsplit
from urllib.error import HTTPError, URLError

import yaml

from .model import ConfigError, Profile, pattern
from .netguard import guarded_fetch

FORMATS = {"auto", "clash", "sing-box", "base64", "uri", "surge"}
SUPPORTED = {"shadowsocks", "trojan", "vmess", "vless", "hysteria2"}
MAX_BYTES = 8 * 1024 * 1024


def decode64(text):
    raw = text.strip().encode("ascii")
    return base64.b64decode(raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True).decode("utf-8-sig")


def boolean(value):
    return str(value).lower() in {"1", "true", "yes"}


def normalize(node, source_id):
    node = {k: v for k, v in node.items() if v is not None}
    proto = str(node.get("protocol", "")).lower()
    node["protocol"] = {"ss": "shadowsocks", "hy2": "hysteria2"}.get(proto, proto)
    try:
        node["port"] = int(node.get("port", 0))
    except (ValueError, TypeError):
        raise ConfigError("节点端口无效") from None
    node["name"] = str(node.get("name") or node.get("server") or "未命名节点")
    node["source"] = source_id
    node["enabled"] = True
    # Different accounts or transports on a shared endpoint are distinct nodes.
    # Hash credentials locally; no plaintext credential appears in the ID.
    identity = json.dumps({k: v for k, v in node.items() if k not in {"name", "enabled", "id", "region"}}, ensure_ascii=False, sort_keys=True)
    node["id"] = "n_" + hashlib.sha256(identity.encode()).hexdigest()[:20]
    # The compiler and subscription parser cross the same validation interface.
    wrapper = {"schema_version": 1, "sources": [{"id": source_id, "name": "source"}], "nodes": [node], "fallback": "DIRECT"}
    return Profile(wrapper).data["nodes"][0]


def uri_node(line):
    line = line.strip()
    if line.startswith("vmess://") and "@" not in line.split("#", 1)[0]:
        v = json.loads(decode64(line[8:].split("#", 1)[0]))
        n = {"protocol": "vmess", "name": v.get("ps"), "server": v.get("add"), "port": v.get("port"), "uuid": v.get("id"), "alter_id": int(v.get("aid") or 0), "security": v.get("scy") or "auto"}
        if v.get("tls") in {"tls", True, "1"}:
            n["tls"] = {"enabled": True, "server_name": v.get("sni") or v.get("host") or v.get("add"), "insecure": False}
        network = v.get("net") or "tcp"
        if network == "ws":
            n["transport"] = {"type": "ws", "path": v.get("path") or "/", "headers": {"Host": v["host"]} if v.get("host") else {}}
        elif network == "grpc":
            n["transport"] = {"type": "grpc", "service_name": v.get("path") or ""}
        elif network != "tcp":
            raise ConfigError("VMess 传输尚未支持：" + str(network))
        return n
    if line.startswith("ss://"):
        remainder = line[5:]
        base, _, fragment = remainder.partition("#")
        if "@" not in base:
            line = "ss://" + decode64(base) + ("#" + fragment if fragment else "")
    u = urlsplit(line)
    kind = {"ss": "shadowsocks", "hy2": "hysteria2"}.get(u.scheme.lower(), u.scheme.lower())
    if kind not in SUPPORTED:
        raise ConfigError("未支持的协议：" + u.scheme)
    q = {k: values[-1] for k, values in parse_qs(u.query, keep_blank_values=True).items()}
    n = {"protocol": kind, "name": unquote(u.fragment) or u.hostname, "server": u.hostname, "port": u.port}
    if kind == "shadowsocks":
        info = unquote((u.netloc.rsplit("@", 1)[0]))
        if ":" not in info:
            info = decode64(info)
        method, separator, password = info.partition(":")
        if not separator or q.get("plugin"):
            raise ConfigError("Shadowsocks 凭据无效或含未支持插件")
        n.update(method=method, password=password)
        return n
    if kind in {"vless", "vmess"}:
        n["uuid"] = unquote(u.username or "")
    else:
        n["password"] = unquote(u.username or "") + ((":" + unquote(u.password)) if u.password else "")
    security = q.get("security", "tls" if kind in {"trojan", "hysteria2"} else "none")
    if security in {"tls", "reality"} or kind in {"trojan", "hysteria2"}:
        n["tls"] = {"enabled": True, "server_name": q.get("sni") or q.get("peer") or u.hostname, "insecure": boolean(q.get("insecure", q.get("allowInsecure", False)))}
        if q.get("fp") and kind != "hysteria2":
            n["tls"]["fingerprint"] = q["fp"]
        if q.get("alpn"):
            n["tls"]["alpn"] = q["alpn"].split(",")
        if security == "reality":
            n["tls"]["reality"] = {"public_key": q.get("pbk", ""), "short_id": q.get("sid", "")}
    elif security not in {"none", ""}:
        raise ConfigError("未支持的安全类型")
    if q.get("flow"):
        n["flow"] = q["flow"]
    network = q.get("type", "tcp")
    if kind != "hysteria2":
        if network == "ws":
            n["transport"] = {"type": "ws", "path": q.get("path", "/"), "headers": {"Host": q["host"]} if q.get("host") else {}}
        elif network == "grpc":
            n["transport"] = {"type": "grpc", "service_name": q.get("serviceName", "")}
        elif network not in {"tcp", ""}:
            raise ConfigError("未支持的传输：" + network)
    if kind == "hysteria2" and q.get("obfs"):
        n["obfs"] = {"type": q["obfs"], "password": q.get("obfs-password", "")}
    if q.get("encryption", "none") != "none":
        raise ConfigError("未支持的 VLESS 加密扩展")
    return n


def clash_node(v):
    if not isinstance(v, dict):
        raise ConfigError("节点记录不是对象")
    kind = {"ss": "shadowsocks", "hy2": "hysteria2"}.get(v.get("type"), v.get("type"))
    if kind not in SUPPORTED:
        raise ConfigError("未支持的协议：" + str(kind))
    for key in ("plugin", "dialer-proxy", "smux", "multiplex", "ports", "private-key", "ech-opts"):
        if v.get(key):
            raise ConfigError("未支持的节点扩展：" + key)
    n = {"name": v.get("name"), "protocol": kind, "server": v.get("server"), "port": v.get("port"), "udp": v.get("udp", True)}
    for key in ("password", "uuid", "flow"):
        if key in v:
            n[key] = v[key]
    if kind == "shadowsocks":
        n["method"] = v.get("cipher")
    if kind == "vmess":
        n.update(alter_id=int(v.get("alterId", 0)), security=v.get("cipher", "auto"))
    if v.get("tls") or kind in {"trojan", "hysteria2"}:
        n["tls"] = {"enabled": True, "server_name": v.get("servername") or v.get("sni") or v.get("server"), "insecure": v.get("skip-cert-verify", False)}
        if v.get("alpn"):
            n["tls"]["alpn"] = v["alpn"]
        if v.get("client-fingerprint") and kind != "hysteria2":
            n["tls"]["fingerprint"] = v["client-fingerprint"]
        if v.get("reality-opts"):
            n["tls"]["reality"] = {"public_key": v["reality-opts"].get("public-key", ""), "short_id": v["reality-opts"].get("short-id", "")}
    network = v.get("network", "tcp")
    if network == "ws":
        opts = v.get("ws-opts", {})
        if set(opts) - {"path", "headers"}:
            raise ConfigError("未支持的 WS 扩展")
        n["transport"] = {"type": "ws", "path": opts.get("path", "/"), "headers": opts.get("headers", {})}
    elif network == "grpc":
        n["transport"] = {"type": "grpc", "service_name": v.get("grpc-opts", {}).get("grpc-service-name", "")}
    elif network != "tcp":
        raise ConfigError("未支持的传输：" + str(network))
    if v.get("obfs"):
        n["obfs"] = {"type": v["obfs"], "password": v.get("obfs-password", "")}
    return n


def singbox_node(v):
    if v.get("type") not in SUPPORTED:
        raise ConfigError("未支持的协议：" + str(v.get("type")))
    blocked = {"detour", "multiplex", "packet_encoding", "udp_over_tcp"}
    if any(v.get(k) for k in blocked):
        raise ConfigError("含未支持的链式或传输扩展")
    n = {"protocol": v["type"], "name": v.get("tag"), "server": v.get("server"), "port": v.get("server_port")}
    for field in ("password", "uuid", "flow", "method", "alter_id", "security", "transport", "obfs"):
        if field in v:
            n[field] = v[field]
    if v.get("tls"):
        tls = v["tls"]
        if set(tls) - {"enabled", "server_name", "insecure", "utls", "reality", "alpn"}:
            raise ConfigError("含未支持的 TLS 扩展")
        n["tls"] = {k: tls[k] for k in ("enabled", "server_name", "insecure", "alpn") if k in tls}
        if tls.get("utls", {}).get("enabled"):
            n["tls"]["fingerprint"] = tls["utls"].get("fingerprint", "chrome")
        if tls.get("reality", {}).get("enabled"):
            n["tls"]["reality"] = {k: tls["reality"][k] for k in ("public_key", "short_id") if k in tls["reality"]}
    if v.get("network") == "tcp":
        n["udp"] = False
    return n


def surge_nodes(text):
    found = []
    in_section = "[Proxy]" not in text
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_section = line.lower() == "[proxy]"
            continue
        if not in_section or not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        name, values = line.split("=", 1)
        parts = next(csv.reader([values], skipinitialspace=True))
        if len(parts) < 3:
            continue
        options = dict(p.split("=", 1) for p in parts[3:] if "=" in p)
        kind = {"ss": "shadowsocks"}.get(parts[0].strip(), parts[0].strip())
        v = {"protocol": kind, "name": name.strip(), "server": parts[1].strip(), "port": parts[2].strip(), "password": options.get("password")}
        if kind == "shadowsocks":
            v["method"] = options.get("encrypt-method")
        if kind == "vmess":
            v.pop("password", None)
            v["uuid"] = options.get("username")
        if boolean(options.get("tls", kind == "trojan")):
            v["tls"] = {"enabled": True, "server_name": options.get("sni") or parts[1].strip(), "insecure": boolean(options.get("skip-cert-verify", False))}
        if boolean(options.get("ws", False)):
            v["transport"] = {"type": "ws", "path": options.get("ws-path", "/"), "headers": {}}
        found.append(v)
    return found


def parse_subscription(content, source):
    try:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    except UnicodeError:
        raise ConfigError("订阅不是 UTF-8 文本；AGE 加密订阅请先解密为普通订阅") from None
    mode = source.get("format", "auto")
    if mode not in FORMATS:
        raise ConfigError("未知订阅格式")
    detected = mode
    text = text.strip()
    if not text or text.startswith(("<!DOCTYPE", "<html", "<HTML")):
        raise ConfigError("订阅为空或返回网页，请检查地址和 UA")
    if text.startswith(("age-encryption.org", "-----BEGIN AGE ENCRYPTED FILE-----")):
        raise ConfigError("该订阅已 AGE 加密；请先使用解密后的订阅地址")
    try:
        if mode == "auto":
            if text.startswith(("{", "[")) and not text.startswith("[Proxy]"):
                detected = "sing-box" if '"outbounds"' in text else "clash"
            elif re.search(r"(?m)^\s*proxies\s*:", text):
                detected = "clash"
            elif re.search(r"(?m)^\s*\[Proxy\]", text):
                detected = "surge"
            elif "://" in text:
                detected = "uri"
            else:
                detected = "base64"
        if detected == "base64":
            text = decode64(re.sub(r"\s+", "", text))
            raw = [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith("#")]
            convert = uri_node
        elif detected == "uri":
            raw = [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith("#")]
            convert = uri_node
        elif detected == "surge":
            raw, convert = surge_nodes(text), lambda x: x
        elif detected == "sing-box":
            value = json.loads(text)
            raw = [v for v in value.get("outbounds", []) if v.get("type") not in {"selector", "urltest", "direct", "block", "dns"}]
            convert = singbox_node
        else:
            value = yaml.safe_load(text)
            raw = value.get("proxies", []) if isinstance(value, dict) else value
            convert = clash_node
        if not isinstance(raw, list) or not raw:
            raise ConfigError("没有识别到节点，请核对订阅格式或 UA")
        if len(raw) > 5000:
            raise ConfigError("单订阅最多支持 5000 条节点")
    except ConfigError:
        raise
    except Exception:
        raise ConfigError("订阅解析失败，请核对格式；已保留原节点") from None
    include = pattern(source.get("include", ""), "包含正则")
    exclude = pattern(source.get("exclude", ""), "排除正则")
    disabled = set(source.get("disabled_protocols", []))
    nodes, skipped, filtered, seen = [], [], 0, set()
    for index, record in enumerate(raw):
        try:
            hinted = record.get("type", record.get("protocol", "")) if isinstance(record, dict) else str(record).split("://", 1)[0]
            hinted = {"ss": "shadowsocks", "hy2": "hysteria2"}.get(hinted, hinted)
            if hinted in disabled:
                filtered += 1
                continue
            node = normalize(convert(record), source["id"])
            if node["protocol"] in disabled or (include and not include.search(node["name"])) or (exclude and exclude.search(node["name"])):
                filtered += 1
                continue
            if node["id"] in seen:
                filtered += 1
                continue
            seen.add(node["id"])
            nodes.append(node)
        except Exception as exc:
            # URI payloads may include credentials: never put the input or exception
            # traceback in response logs. Validator messages contain field names only.
            reason = str(exc) if isinstance(exc, ConfigError) else "节点参数不完整或格式无效"
            skipped.append({"index": index + 1, "reason": reason[:200]})
    if not nodes and skipped and not filtered:
        raise ConfigError("识别到节点但全部不受支持：" + skipped[0]["reason"])
    return nodes, {"detected_format": detected, "total": len(raw), "retained": len(nodes), "filtered": filtered, "skipped": len(skipped), "issues": skipped[:30]}


def fetch_subscription(source):
    url = source["url"]
    headers = {"User-Agent": source.get("user_agent") or "clash.meta", "Accept": "*/*"}
    try:
        body, response_headers = guarded_fetch(
            url,
            timeout=source.get("request_timeout", 20),
            max_bytes=MAX_BYTES,
            headers=headers,
            allow_private=source.get("allow_private", False),
            what="订阅地址",
        )
        if len(body) > MAX_BYTES:
            raise ConfigError("订阅超过 8 MiB，已停止读取")
        if response_headers.get("Content-Encoding", "").lower() == "gzip":
            with gzip.GzipFile(fileobj=io.BytesIO(body)) as unpacked:
                body = unpacked.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ConfigError("解压后的订阅超过 8 MiB")
        userinfo = response_headers.get("Subscription-Userinfo", "")
        info = {}
        for part in userinfo.split(";"):
            key, _, value = part.strip().partition("=")
            if key in {"upload", "download", "total", "expire"} and value.isdigit():
                info[key] = int(value)
        nodes, stats = parse_subscription(body, source)
        return nodes, {**stats, "traffic": info}
    except HTTPError as exc:
        raise ConfigError(f"上游返回 HTTP {exc.code}；请检查地址、UA 或订阅状态") from None
    except (URLError, TimeoutError, OSError):
        raise ConfigError("拉取失败：网络、DNS、证书或超时；原节点已保留") from None
