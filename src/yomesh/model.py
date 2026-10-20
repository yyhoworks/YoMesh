"""One validated model feeds every target; unsupported behavior fails explicitly."""
from __future__ import annotations

import ipaddress
import re
import uuid
from copy import deepcopy


class ConfigError(ValueError):
    pass


PROTOCOLS = {"shadowsocks", "trojan", "vmess", "vless", "hysteria2"}
MODES = {"select", "url-test", "fallback", "load-balance"}
MATCHES = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX", "IP-CIDR", "IP-CIDR6", "RULE-SET"}
BUILTINS = {"DIRECT", "REJECT"}


def fail(path, message):
    raise ConfigError(f"{path}: {message}")


def require_string(value, path):
    if not isinstance(value, str) or not value.strip():
        fail(path, "需要非空文本")
    return value


def check_domain(value, path):
    require_string(value, path)
    try:
        domain = value.rstrip(".").encode("idna").decode("ascii")
    except UnicodeError:
        fail(path, "域名编码无效")
    if len(domain) > 253 or not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", part) for part in domain.split(".")):
        fail(path, "需要有效域名，不能包含路径、通配符或空格")


def check_network(value, path):
    try:
        ipaddress.ip_network(value, strict=False)
    except (ValueError, TypeError):
        fail(path, "需要有效 IP-CIDR")


def pattern(value, path):
    if not value:
        return None
    try:
        return re.compile(value.replace("(?i)", ""), re.IGNORECASE)
    except (re.error, TypeError):
        fail(path, "正则表达式无效")


def match_value(kind, value, path):
    require_string(value, path)
    if kind.startswith("IP-CIDR"):
        check_network(value, path)
    elif kind == "DOMAIN-REGEX":
        # Domain regexes are case-sensitive unless the expression opts in.
        try:
            re.compile(value)
        except re.error:
            fail(path, "域名正则无效")
    elif kind == "DOMAIN-KEYWORD":
        if any(c in value for c in "\r\n,"):
            fail(path, "域名关键词不能包含换行或逗号")
    else:
        check_domain(value, path)


def classical_entry(entry, path):
    require_string(entry, path)
    kind, comma, value = entry.partition(",")
    if not comma or kind not in MATCHES - {"RULE-SET"}:
        fail(path, "不支持的 classical 匹配类型")
    match_value(kind, value, path)
    return kind, value


class Profile:
    def __init__(self, value):
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            fail("schema_version", "仅支持 YoMesh profile v1；原型演示 JSON 需先补全节点信息")
        self.data = deepcopy(value)
        self.index = {}
        self.criteria_cache = {}
        for kind in ("sources", "nodes", "groups", "library", "policies"):
            entries = self.data.setdefault(kind, [])
            if not isinstance(entries, list):
                fail(kind, "需要数组")
            table = self.index[kind] = {}
            for i, item in enumerate(entries):
                path = f"{kind}[{i}]"
                if not isinstance(item, dict):
                    fail(path, "需要对象")
                ident = item.get("id")
                if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident) or ident in BUILTINS:
                    fail(path + ".id", "需要 1–64 位字母、数字、下划线或连字符")
                if ident in table:
                    fail(path + ".id", "ID 重复")
                table[ident] = item
                require_string(item.get("name"), path + ".name")
                if "enabled" in item and not isinstance(item["enabled"], bool):
                    fail(path + ".enabled", "需要布尔值")
        # References from targets share a namespace.
        if set(self.index["nodes"]) & set(self.index["groups"]):
            fail("groups", "节点与节点组 ID 不能重名")
        for node in self.data["nodes"]:
            self._node(node)
        for ruleset in self.data["library"]:
            self._ruleset(ruleset)
        for group in self.data["groups"]:
            self._group(group)
        for group in self.data["groups"]:
            self.members(group["id"])
        for policy in self.data["policies"]:
            self._policy(policy)
        self.target(self.data.get("fallback"), "fallback", reject_allowed=False)
        self.settings = self.data.setdefault("settings", {})
        if not isinstance(self.settings, dict):
            fail("settings", "需要对象")
        defaults = {"mixed_port": 17890, "dns_port": 1053, "local_upstream": "223.5.5.5", "remote_upstream": "tcp://1.1.1.1", "bootstrap": "223.5.5.5", "dns_default": "remote", "dns_socks5": "", "dns_follow_rules": True}
        for key, default in defaults.items():
            self.settings.setdefault(key, default)
        if set(self.settings) - set(defaults):
            fail("settings", "包含尚未支持的设置字段")
        for key in ("mixed_port", "dns_port"):
            port = self.settings[key]
            if type(port) is not int or not 1024 <= port <= 65535:
                fail("settings." + key, "需要 1024–65535 端口")
        if self.settings["mixed_port"] == self.settings["dns_port"]:
            fail("settings", "代理与 DNS 监听端口不能相同")
        if self.settings["dns_default"] not in {"local", "remote"}:
            fail("settings.dns_default", "只能为 local 或 remote")
        if type(self.settings["dns_follow_rules"]) is not bool:
            fail("settings.dns_follow_rules", "需要布尔值")
        socks = self.settings["dns_socks5"]
        if not isinstance(socks, str):
            fail("settings.dns_socks5", "需要 IP:端口；留空使用原代理入口端口")
        if socks:
            from urllib.parse import urlsplit
            try:
                endpoint = urlsplit("//" + socks)
                ipaddress.ip_address(endpoint.hostname)
                if not endpoint.port or endpoint.path or endpoint.query or endpoint.fragment or endpoint.username is not None:
                    raise ValueError()
            except (ValueError, TypeError):
                fail("settings.dns_socks5", "需要 IP:端口，例如 127.0.0.1:17890 或 [::1]:17890")
        for key in ("bootstrap", "local_upstream"):
            try:
                ipaddress.ip_address(self.settings[key])
            except (ValueError, TypeError):
                fail("settings." + key, "第一版要求纯 IP，避免 DNS 启动循环")
        remote = self.settings["remote_upstream"]
        if not isinstance(remote, str) or not remote.startswith(("tcp://", "https://", "tls://")):
            fail("settings.remote_upstream", "代理 DNS 需要 TCP / DoT / DoH 上游")
        from .base_config import parse_bases, effective_settings
        self.base_configs = parse_bases(self.data.get("base_configs", {}))
        self.validate_extensions(self.data)
        self.native_configuration = None
        if "mosdns" in self.data:
            from .mosdns import checked
            self.data["mosdns"] = checked(self.data["mosdns"], self.settings)
        if "native_configuration" in self.data:
            from .native_configuration import checked
            self.native_configuration = checked(self.data["native_configuration"])
        for target in self.base_configs:
            effective_settings(self, target, require_socks=False)

    def validate_extensions(self, value):
        plans = value.get("routing_plans", [])
        if not isinstance(plans, list):
            fail("routing_plans", "需要数组")
        self.routing_plans = {}
        for plan in plans:
            ident = plan.get("id") if isinstance(plan, dict) else None
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident) or ident in self.routing_plans:
                fail("routing_plans", "规则方案 ID 不合法或重复")
            require_string(plan.get("name"), "routing_plans." + ident + ".name")
            if not isinstance(plan.get("policies"), list):
                fail("routing_plans." + ident, "策略需要数组")
            for policy in plan["policies"]:
                self._policy(policy)
            self.target(plan.get("fallback"), "routing_plans." + ident + ".fallback", reject_allowed=False)
            self.routing_plans[ident] = plan
        if plans:
            active = self.routing_plans.get(value.get("active_plan"))
            if not active or active["policies"] != value["policies"] or active["fallback"] != value["fallback"]:
                fail("active_plan", "当前方案与工作区不一致，请先保存更改")
        from .inbounds import validate_inbounds
        self.inbounds = validate_inbounds(value.get("inbounds", []), self.routing_plans, value.get("settings", {}))

    def active(self, node):
        source = node.get("source", "manual")
        return node.get("enabled", True) and (source == "manual" or self.index["sources"][source].get("enabled", True))

    def _node(self, node):
        path = "nodes." + node["id"]
        allowed = {"id", "name", "protocol", "server", "port", "password", "uuid", "method", "tls", "transport", "alter_id", "security", "flow", "source", "enabled", "region", "udp", "obfs"}
        unknown = set(node) - allowed
        if unknown:
            fail(path, "未支持的节点字段：" + ", ".join(sorted(unknown)))
        protocol = str(node.get("protocol", "")).lower()
        node["protocol"] = {"ss": "shadowsocks", "hy2": "hysteria2"}.get(protocol, protocol)
        if node["protocol"] not in PROTOCOLS:
            fail(path + ".protocol", "暂不支持此协议")
        common = {"id", "name", "protocol", "server", "port", "source", "enabled", "region", "tls", "udp"}
        options = {"shadowsocks": {"method", "password"}, "trojan": {"password", "transport"}, "vmess": {"uuid", "transport", "alter_id", "security"}, "vless": {"uuid", "transport", "flow"}, "hysteria2": {"password", "obfs"}}
        if set(node) - common - options[node["protocol"]]:
            fail(path, "包含不适用于此协议的字段")
        server = require_string(node.get("server"), path + ".server")
        try:
            ipaddress.ip_address(server)
        except ValueError:
            check_domain(server, path + ".server")
        if type(node.get("port")) is not int or not 1 <= node["port"] <= 65535:
            fail(path + ".port", "需要 1–65535 端口")
        source = node.setdefault("source", "manual")
        if source != "manual" and source not in self.index["sources"]:
            fail(path + ".source", "订阅来源不存在")
        if node["protocol"] in {"trojan", "hysteria2", "shadowsocks"}:
            require_string(node.get("password"), path + ".password")
        if node["protocol"] == "shadowsocks":
            if node.get("method") not in {"aes-128-gcm", "aes-256-gcm", "chacha20-ietf-poly1305", "2022-blake3-aes-128-gcm", "2022-blake3-aes-256-gcm", "2022-blake3-chacha20-poly1305"}:
                fail(path + ".method", "支持 AEAD 与 Shadowsocks 2022；此加密方法尚未支持")
        if node["protocol"] in {"vless", "vmess"}:
            try:
                uuid.UUID(node.get("uuid", ""))
            except (ValueError, TypeError, AttributeError):
                fail(path + ".uuid", "需要有效 UUID")
        tls = node.setdefault("tls", {})
        if not isinstance(tls, dict) or set(tls) - {"enabled", "server_name", "insecure", "reality", "fingerprint", "alpn"}:
            fail(path + ".tls", "存在未支持的 TLS 字段")
        if "alpn" in tls and (not isinstance(tls["alpn"], list) or not all(isinstance(x, str) for x in tls["alpn"])):
            fail(path + ".tls.alpn", "需要文本数组")
        if "udp" in node and not isinstance(node["udp"], bool):
            fail(path + ".udp", "需要布尔值")
        if node.get("obfs"):
            obfs = node["obfs"]
            if not isinstance(obfs, dict) or obfs.get("type") != "salamander" or not obfs.get("password") or set(obfs) - {"type", "password"}:
                fail(path + ".obfs", "仅支持 Hysteria2 salamander 混淆")
        for key in ("enabled", "insecure"):
            if key in tls and not isinstance(tls[key], bool):
                fail(path + ".tls." + key, "需要布尔值")
        if node["protocol"] in {"trojan", "hysteria2"}:
            if tls.get("enabled") is False:
                fail(path + ".tls", "此协议要求 TLS")
            tls["enabled"] = True
        if node["protocol"] == "shadowsocks" and tls:
            fail(path + ".tls", "Shadowsocks 的独立 TLS 扩展尚未支持")
        if tls and not tls.get("enabled") and set(tls) - {"enabled"}:
            fail(path + ".tls", "TLS 未启用，不能设置握手参数")
        if tls.get("fingerprint") and node["protocol"] == "hysteria2":
            fail(path + ".tls.fingerprint", "Hysteria2 使用 QUIC，不支持 TCP uTLS 指纹")
        if tls.get("reality"):
            r = tls["reality"]
            if node["protocol"] != "vless" or not tls.get("enabled") or not isinstance(r, dict) or set(r) - {"public_key", "short_id"}:
                fail(path + ".tls.reality", "仅支持 VLESS + TLS Reality")
            require_string(r.get("public_key"), path + ".tls.reality.public_key")
            if not re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,8}", r.get("short_id", "")):
                fail(path + ".tls.reality.short_id", "需要 0–16 位偶数长度十六进制")
        if node.get("flow") and (node["protocol"] != "vless" or node["flow"] != "xtls-rprx-vision"):
            fail(path + ".flow", "仅支持 VLESS xtls-rprx-vision")
        if node.get("flow") and (not tls.get("enabled") or node.get("transport")):
            fail(path + ".flow", "Vision 要求 TLS 且不叠加 WS/gRPC")
        transport = node.get("transport")
        if transport:
            if node["protocol"] not in {"vless", "vmess", "trojan"} or not isinstance(transport, dict):
                fail(path + ".transport", "此协议不支持该传输扩展")
            if transport.get("type") not in {"ws", "grpc"}:
                fail(path + ".transport", "第一版只支持 ws 与 grpc")
            if set(transport) - {"type", "path", "headers", "service_name"}:
                fail(path + ".transport", "存在未支持的传输字段")
            fields = {"type", "path", "headers"} if transport["type"] == "ws" else {"type", "service_name"}
            if set(transport) - fields:
                fail(path + ".transport", "包含不适用于所选传输的字段")

    def _ruleset(self, item):
        path = "library." + item["id"]
        if set(item) - {"id", "name", "kind", "entries", "note", "source", "enabled"}:
            fail(path, "规则集包含未支持字段")
        if "source" in item:
            from .rule_sources import clean_source
            clean_source(item["source"])
        if item.get("kind") not in {"domain", "ipcidr", "classical"}:
            fail(path + ".kind", "规则集支持 domain / ipcidr / classical")
        entries = item.get("entries")
        if not isinstance(entries, list) or not entries:
            fail(path + ".entries", "规则集内容不能为空；远程规则需先拉取为快照")
        criteria = []
        for entry in entries:
            if item["kind"] == "domain":
                check_domain(entry[2:] if isinstance(entry, str) and entry.startswith("+.") else entry, path)
                criteria.append(("DOMAIN-SUFFIX", entry[2:]) if entry.startswith("+.") else ("DOMAIN", entry))
            elif item["kind"] == "ipcidr":
                check_network(entry, path)
                criteria.append(("IP-CIDR6" if ":" in entry else "IP-CIDR", str(ipaddress.ip_network(entry, strict=False))))
            else:
                criteria.append(classical_entry(entry, path))
        self.criteria_cache[item["id"]] = criteria

    def _group(self, group):
        path = "groups." + group["id"]
        if set(group) - {"id", "name", "mode", "sources", "nodes", "groups", "include", "exclude", "interval", "tolerance", "order", "selected", "enabled", "test_url", "test_timeout_ms"}:
            fail(path, "包含尚未支持的节点组字段")
        if group.get("mode") not in MODES:
            fail(path + ".mode", "未知节点组模式")
        from urllib.parse import urlsplit
        if "test_url" in group:
            value = group["test_url"]
            if not isinstance(value, str) or len(value) > 8192 or urlsplit(value).scheme not in {"http", "https"} or not urlsplit(value).hostname:
                fail(path + ".test_url", "测速需要 HTTP / HTTPS 地址")
        if "test_timeout_ms" in group and (type(group["test_timeout_ms"]) is not int or not 500 <= group["test_timeout_ms"] <= 30000):
            fail(path + ".test_timeout_ms", "手动测速超时需要 500–30000 毫秒")
        if "enabled" in group and not isinstance(group["enabled"], bool):
            fail(path + ".enabled", "需要布尔值")
        for key, default, minimum in (("interval", 300, 1), ("tolerance", 50, 0)):
            value = group.setdefault(key, default)
            if type(value) is not int or value < minimum:
                fail(path + "." + key, "需要有效整数")
        for key, kind in (("sources", "sources"), ("nodes", "nodes"), ("groups", "groups")):
            values = group.setdefault(key, [])
            if not isinstance(values, list):
                fail(path + "." + key, "需要引用 ID 数组")
            for ident in values:
                if ident not in self.index[kind]:
                    fail(path + "." + key, "引用不存在：" + str(ident))
        for key in ("include", "exclude"):
            pattern(group.setdefault(key, ""), path + "." + key)
        order = group.get("order", [])
        if not isinstance(order, list) or not all(isinstance(i, str) for i in order) or len(order) != len(set(order)):
            fail(path + ".order", "成员顺序需要不重复的 ID 数组")
        if group.get("selected") is not None and (group["mode"] != "select" or not isinstance(group["selected"], str)):
            fail(path + ".selected", "仅手动组支持指定默认成员")

    def members(self, ident, trail=(), allow_empty=False):
        if ident in trail:
            fail("groups." + ident, "循环引用：" + " → ".join((*trail, ident)))
        group = self.index["groups"][ident]
        if not group.get("enabled", True):
            return []
        include = pattern(group["include"], "groups." + ident + ".include")
        exclude = pattern(group["exclude"], "groups." + ident + ".exclude")
        nodes = [n["id"] for n in self.data["nodes"] if self.active(n) and (n["id"] in group["nodes"] or n["source"] in group["sources"]) and (not include or include.search(n["name"])) and (not exclude or not exclude.search(n["name"]))]
        child_groups = [ref for ref in group["groups"] if self.index["groups"][ref].get("enabled", True)]
        for ref in child_groups:
            self.members(ref, (*trail, ident), allow_empty=allow_empty)
        result = list(dict.fromkeys(nodes + child_groups))
        priority = {node: i for i, node in enumerate(group.get("order", []))}
        result.sort(key=lambda node: priority.get(node, len(priority)))
        selected = group.get("selected")
        if selected:
            if selected not in result:
                fail("groups." + ident + ".selected", "所选成员不在当前有效成员内")
            result = [selected] + [node for node in result if node != selected]
        if not result and not allow_empty:
            fail("groups." + ident, "没有有效成员，请检查来源启用与筛选条件")
        return result

    def target(self, value, path, reject_allowed=True):
        if value in BUILTINS:
            if value == "REJECT" and not reject_allowed:
                fail(path, "此处不支持 REJECT")
            return
        if value in self.index["groups"]:
            if not self.index["groups"][value].get("enabled", True):
                fail(path, "出口节点组已停用，请先更换规则的出口")
            return
        if value not in self.index["nodes"] or not self.active(self.index["nodes"][value]):
            fail(path, "出口不存在或已停用")

    @classmethod
    def for_dns(cls, value):
        """Resolve DNS's rule view without depending on proxy members or bases."""
        profile = cls({"schema_version": 1, "library": value.get("library", []), "settings": value.get("settings", {}), "fallback": "DIRECT", **({"mosdns": value["mosdns"]} if "mosdns" in value else {})})
        policies = deepcopy(value.get("policies", []))
        seen = set()
        for policy in policies:
            profile._policy(policy, validate_target=False)
            if policy["id"] in seen or ("enabled" in policy and type(policy["enabled"]) is not bool):
                fail("policies", "策略 ID 重复或启用字段无效")
            seen.add(policy["id"])
        profile.data["policies"] = policies
        return profile

    def _policy(self, policy, validate_target=True):
        if not isinstance(policy, dict):
            fail("policies", "策略需要对象")
        ident = policy.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident):
            fail("policies", "策略 ID 无效")
        path = "policies." + policy["id"]
        require_string(policy.get("name"), path + ".name")
        if set(policy) - {"id", "name", "type", "value", "target", "enabled", "condition_groups", "group_operator", "dns"}:
            fail(path, "包含尚未支持的策略字段")
        if policy.get("type") not in MATCHES | {"LOGICAL"}:
            fail(path + ".type", "未支持的匹配类型")
        from .rulelogic import expression
        expression(policy, self.index["library"])
        if policy.get("dns", "auto") not in {"auto", "local", "remote"}:
            fail(path + ".dns", "DNS 需要自动、本地或代理上游")
        if policy["type"] != "LOGICAL" and ("condition_groups" in policy or "group_operator" in policy):
            fail(path, "条件组需要使用 LOGICAL 类型")
        if validate_target:
            self.target(policy.get("target"), path + ".target")
        else:
            require_string(policy.get("target"), path + ".target")

    def tag(self, ident):
        if ident in BUILTINS:
            return ident
        return ("group_" if ident in self.index["groups"] else "node_") + ident

    def enabled_policies(self):
        return [p for p in self.data["policies"] if p.get("enabled", True)]

    def runtime_policies(self):
        """Collect assets for the default entry and enabled inbound plans."""
        result = list(self.data["policies"])
        active = self.data.get("active_plan")
        bound = dict.fromkeys(i["plan"] for i in self.inbounds if i.get("enabled", True) and i["plan"] != active)
        for ident in bound:
            result.extend(self.routing_plans[ident]["policies"])
        return [p for p in result if p.get("enabled", True)]

    @staticmethod
    def routing_usage(value):
        """Explain validated workspace drafts without requiring nonempty groups."""
        from .rulelogic import references, expression, describe
        library = {item["id"]: item for item in value.get("library", [])}
        active = value.get("active_plan")
        plans = deepcopy(value.get("routing_plans") or [{"id": active, "name": "当前工作区", "policies": value.get("policies", [])}])
        bound = {}
        for inbound in value.get("inbounds", []):
            if inbound.get("enabled", True):
                bound.setdefault(inbound["plan"], []).append(inbound["name"])
        usage = {ident: {"references": [], "runtime": False} for ident in library}
        plan_views = []
        for plan in plans:
            current = plan["id"] == active
            policies = value.get("policies", []) if current else plan["policies"]
            runtime = current or plan["id"] in bound
            view = {"id": plan["id"], "name": plan["name"], "current": current, "runtime": runtime, "inbounds": bound.get(plan["id"], []), "policies": []}
            for policy in policies:
                refs = sorted(references(policy))
                counts = {"local": 0, "subscription": 0}
                for ident in refs:
                    counts["subscription" if library[ident].get("source", {}).get("type") == "url" else "local"] += 1
                    item = usage[ident]
                    item["references"].append({"plan": plan["id"], "plan_name": plan["name"], "policy": policy["id"], "name": policy["name"], "enabled": policy.get("enabled", True), "current": current, "runtime": runtime and policy.get("enabled", True)})
                    item["runtime"] |= runtime and policy.get("enabled", True)
                view["policies"].append({"id": policy["id"], "name": policy["name"], "enabled": policy.get("enabled", True), "summary": describe(expression(policy, library), library), "references": refs, "sources": counts})
            plan_views.append(view)
        settings = Profile({"schema_version": 1, "fallback": "DIRECT", "settings": value.get("settings", {})}).settings
        return {"plans": plan_views, "library": usage, "settings": settings, "included": sorted(i for i, item in usage.items() if item["runtime"]), "excluded": sorted(i for i, item in usage.items() if not item["runtime"]), "dns_scope": "仅当前方案的域名条件；IP 条件与入站独立方案由代理路由执行"}

    def criteria(self, policy):
        if policy["type"] == "LOGICAL":
            fail("policies." + policy["id"], "组合条件必须按逻辑表达式编译，不能展开丢失关系")
        if policy["type"] == "RULE-SET":
            return self.criteria_cache[policy["value"]]
        return [(policy["type"], policy["value"])]
