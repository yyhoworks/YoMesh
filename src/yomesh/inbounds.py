"""Portable inbound fields and native adapters; no host network mutations."""
from __future__ import annotations

import ipaddress
from copy import deepcopy

from .model import fail, require_string

METHODS = {"aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm", "2022-blake3-chacha20-poly1305"}


def validate_inbounds(values, plans, settings):
    if not isinstance(values, list):
        fail("inbounds", "需要入站数组")
    seen, ports = set(), set()
    allowed = {"id", "name", "protocol", "listen", "port", "enabled", "plan", "home", "sniff", "password", "method", "users", "certificate", "private_key", "alpn", "obfs", "up_mbps", "down_mbps"}
    for inbound in values:
        path = "inbounds"
        if not isinstance(inbound, dict) or set(inbound) - allowed:
            fail(path, "包含未支持的入站字段")
        ident = require_string(inbound.get("id"), path + ".id")
        if ident in seen or not all(c.isascii() and (c.isalnum() or c in "_-") for c in ident) or len(ident) > 64:
            fail(path + ".id", "入站 ID 无效或重复")
        seen.add(ident)
        path += "." + ident
        require_string(inbound.get("name"), path + ".name")
        protocol = inbound.get("protocol")
        if protocol not in {"mixed", "shadowsocks", "trojan", "hysteria2"}:
            fail(path, "仅支持 mixed、SS、Trojan、Hysteria2 入站")
        try:
            ipaddress.ip_address(inbound.get("listen"))
        except (ValueError, TypeError):
            fail(path + ".listen", "监听地址需要 IP")
        port = inbound.get("port")
        if type(port) is not int or not 1024 <= port <= 65535:
            fail(path + ".port", "端口需要为 1024–65535")
        for flag in ("enabled", "home", "sniff"):
            if flag in inbound and not isinstance(inbound[flag], bool):
                fail(path + "." + flag, "需要布尔值")
        if inbound.get("plan") not in plans:
            fail(path + ".plan", "绑定的规则方案不存在")
        if inbound.get("enabled", True):
            # Conservative cross-platform collision check, including wildcard binds.
            if port in ports or port in {settings.get("mixed_port", 17890), settings.get("dns_port", 1053)}:
                fail(path + ".port", "入站端口与其他监听冲突")
            ports.add(port)
        if protocol != "mixed":
            require_string(inbound.get("password"), path + ".password")
        if protocol == "shadowsocks" and inbound.get("method") not in METHODS:
            fail(path + ".method", "未支持的 SS 加密方法")
        if protocol in {"trojan", "hysteria2"}:
            for field, header in (("certificate", "-----BEGIN CERTIFICATE-----"), ("private_key", "-----BEGIN")):
                value = require_string(inbound.get(field), path + "." + field)
                if header not in value:
                    fail(path + "." + field, "需要 PEM 内容，不能引用 MR 原设备路径")
        if "alpn" in inbound and (not isinstance(inbound["alpn"], list) or not all(isinstance(v, str) for v in inbound["alpn"])):
            fail(path + ".alpn", "需要文本数组")
        users = inbound.get("users", [])
        if not isinstance(users, list):
            fail(path + ".users", "用户需要数组")
        for user in users:
            if not isinstance(user, dict) or set(user) != {"username", "password"}:
                fail(path + ".users", "用户需要用户名与密码")
            require_string(user["username"], path + ".users")
            require_string(user["password"], path + ".users")
        if users and protocol != "mixed":
            fail(path + ".users", "多用户只支持 mixed；其他入站使用协议密码")
        if inbound.get("obfs"):
            obfs = inbound["obfs"]
            if protocol != "hysteria2" or not isinstance(obfs, dict) or set(obfs) != {"type", "password"} or obfs["type"] != "salamander":
                fail(path + ".obfs", "仅支持 Hysteria2 salamander")
            require_string(obfs["password"], path + ".obfs.password")
        for field in ("up_mbps", "down_mbps"):
            if field in inbound and (type(inbound[field]) is not int or inbound[field] < 0):
                fail(path + "." + field, "带宽需要非负整数")
    return values


def tag(inbound):
    return "in_" + inbound["id"]


def mihomo_listener(inbound, active_plan):
    protocol = inbound["protocol"]
    result = {"name": tag(inbound), "type": protocol, "listen": inbound["listen"], "port": str(inbound["port"])}
    if inbound["plan"] != active_plan:
        result["rule"] = "plan_" + inbound["plan"]
    if protocol == "shadowsocks":
        result.update(cipher=inbound["method"], password=inbound["password"], udp=True)
    elif protocol in {"trojan", "hysteria2"}:
        result.update(certificate=inbound["certificate"], **{"private-key": inbound["private_key"]})
        result["users"] = {"home": inbound["password"]} if protocol == "hysteria2" else [{"username": "home", "password": inbound["password"]}]
        if protocol == "hysteria2":
            if inbound.get("alpn"):
                result["alpn"] = inbound["alpn"]
            for field, native in (("up_mbps", "up"), ("down_mbps", "down")):
                if inbound.get(field):
                    result[native] = str(inbound[field]) + " Mbps"
            if inbound.get("obfs"):
                result.update(obfs=inbound["obfs"]["type"], **{"obfs-password": inbound["obfs"]["password"]})
    elif inbound.get("users"):
        result["users"] = deepcopy(inbound["users"])
    return result


def singbox_listener(inbound):
    protocol = inbound["protocol"]
    result = {"tag": tag(inbound), "type": protocol, "listen": inbound["listen"], "listen_port": inbound["port"]}
    if protocol == "shadowsocks":
        result.update(method=inbound["method"], password=inbound["password"])
    elif protocol in {"trojan", "hysteria2"}:
        result["users"] = [{"name": "home", "password": inbound["password"]}]
        result["tls"] = {"enabled": True, "certificate": inbound["certificate"].splitlines(), "key": inbound["private_key"].splitlines()}
        if inbound.get("alpn"):
            result["tls"]["alpn"] = inbound["alpn"]
        if protocol == "hysteria2":
            for field in ("up_mbps", "down_mbps", "obfs"):
                if inbound.get(field):
                    result[field] = deepcopy(inbound[field])
    elif inbound.get("users"):
        result["users"] = deepcopy(inbound["users"])
    return result
