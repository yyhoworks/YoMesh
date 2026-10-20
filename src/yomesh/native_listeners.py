"""Patch native listeners without rebuilding or narrowing their original fields."""
from copy import deepcopy

from .model import ConfigError
from .native_configuration import checked

FIELDS = {"name", "type", "listen", "port", "cipher", "password", "users", "certificate", "private-key", "alpn", "udp", "proxy", "rule", "routing-mark"}
PROTOCOLS = {"mixed", "http", "socks", "shadowsocks", "trojan", "hysteria2"}


def change_listener(native, request):
    result = deepcopy(native)
    listeners = result["mihomo"].setdefault("listeners", [])
    name = request.get("name")
    index = next((i for i, item in enumerate(listeners) if item["name"] == name), None)
    if name is not None and index is None:
        raise ConfigError("入站已变化，请刷新后重试")
    original = listeners[index] if index is not None else {}
    changes = request.get("changes", {})
    if not isinstance(changes, dict) or set(changes) - FIELDS:
        raise ConfigError("入站表单包含未支持的字段，请使用高级原生参数")
    if name and any(key in changes and changes[key] != original.get(key) for key in ("name", "type")):
        raise ConfigError("已有入站名称与协议保持固定，避免原规则引用失效")
    listener = deepcopy(original)
    if "advanced" in request:
        advanced = request["advanced"]
        if not isinstance(advanced, dict) or set(advanced) & FIELDS:
            raise ConfigError("高级参数需要 JSON 对象，不能重复填写表单管理的字段")
        listener = {**deepcopy(advanced), **{k: deepcopy(v) for k, v in original.items() if k in FIELDS}}
    for key, value in changes.items():
        if value is None:
            listener.pop(key, None)
        else:
            listener[key] = deepcopy(value)
    if listener.get("type") not in PROTOCOLS:
        raise ConfigError("此协议请使用完整原生 JSON 编辑，原字段不会丢弃")
    if not isinstance(listener.get("name"), str) or not listener["name"].strip():
        raise ConfigError("请填写入站名称")
    # Existing port expressions and optional fields survive an unchanged save.
    if index is None or "port" in changes:
        port = listener.get("port")
        if isinstance(port, str) and port.isdecimal():
            port = int(port)
        if type(port) is not int or not 1 <= port <= 65535:
            raise ConfigError("入站端口需要为 1–65535；已有端口表达式请用原生 JSON 编辑")
    for key in ("listen", "cipher", "password", "certificate", "private-key", "proxy", "rule"):
        if key in listener and (not isinstance(listener[key], str) or not listener[key].strip()):
            raise ConfigError("入站文本字段不能为空；可清空可选字段以移除它")
    if "udp" in listener and type(listener["udp"]) is not bool:
        raise ConfigError("UDP 开关需要布尔值")
    if "routing-mark" in listener and (type(listener["routing-mark"]) is not int or listener["routing-mark"] < 0):
        raise ConfigError("路由标记需要非负整数")
    if "alpn" in listener and (not isinstance(listener["alpn"], list) or not all(isinstance(v, str) and v for v in listener["alpn"])):
        raise ConfigError("ALPN 需要非空文本列表")
    kind = listener["type"]
    if kind == "shadowsocks" and (not listener.get("cipher") or not listener.get("password")):
        raise ConfigError("Shadowsocks 需要加密方式和密码")
    if kind in {"trojan", "hysteria2"} and any(not listener.get(k) for k in ("certificate", "private-key", "users")):
        raise ConfigError("Trojan / Hysteria2 需要用户、证书和私钥")
    if "users" in listener and (index is None or "users" in changes):
        users = listener["users"]
        if kind == "hysteria2":
            valid = isinstance(users, dict) and all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in users.items())
        else:
            valid = isinstance(users, list) and all(isinstance(u, dict) and isinstance(u.get("username"), str) and u["username"] and isinstance(u.get("password"), str) and u["password"] for u in users)
            if valid and len({u["username"] for u in users}) != len(users):
                raise ConfigError("入站用户名不能重复")
        if not valid:
            raise ConfigError("入站用户需要完整的用户名和密码")
    if index is None:
        listeners.append(listener)
    else:
        listeners[index] = listener
    return checked(result)
