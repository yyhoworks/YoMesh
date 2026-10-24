"""Native editing helpers. Previews use the saved Profile and pinned core."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import uuid
import yaml

from .model import ConfigError, Profile, check_domain
from .native import find_core, write_bundle_files, write_json
from .native_configuration import checked, load_yaml, rule_yaml_entries, safe_path
from .native_policy import _children, split_target

_core_slots = threading.BoundedSemaphore(2)


def wait_native_ready(api, config, timeout=8):
    """The controller can answer /version before file providers finish loading."""
    names = {p["name"] for p in config.get("proxies", [])} | {g["name"] for g in config.get("proxy-groups", [])}
    sources = set(config.get("proxy-providers", {}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proxies = api('/proxies').get('proxies', {})
            providers = api('/providers/proxies').get('providers', {}) if sources else {}
            if names <= set(proxies) and all(name in providers and providers[name].get('updatedAt') and not providers[name]['updatedAt'].startswith('0001-') for name in sources):
                return
        except (OSError, ValueError):
            pass
        time.sleep(.05)
    raise ConfigError("内核已启动，但节点缓存尚未就绪；请检查来源缓存后重试")


def node_catalog(native, private=False):
    rows = []
    sources = [("手动", native["mihomo"].get("proxies", []), "manual")]
    for name, provider in native["mihomo"].get("proxy-providers", {}).items():
        body = native.get("documents", {}).get(str(provider.get("path", "")).removeprefix("./"))
        try:
            values = provider.get("payload", []) if provider.get("type") == "inline" else load_yaml(body).get("proxies", []) if body else []
        except (yaml.YAMLError, ValueError, TypeError, AttributeError):
            raise ConfigError("节点来源缓存格式无效") from None
        sources.append((name, values, "subscription"))
    for source, values, origin in sources:
        if not isinstance(values, list) or any(not isinstance(n, dict) or not isinstance(n.get("name"), str) or not n["name"] for n in values):
            raise ConfigError("节点来源需要带名称的节点列表")
        for node in values:
            key = hashlib.sha256((origin + "\0" + source + "\0" + json.dumps(node, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:24]
            rows.append({"id": key, "name": node["name"], "type": node.get("type", ""), "server": node.get("server"), "port": node.get("port"), "provider": source,
                         "origin": origin, **({"raw": deepcopy(node)} if private else {})})
    return rows


@contextmanager
def native_core(root, native, group=None):
    """No inbound listeners or remote provider refresh; credentials stay in temp files."""
    if not _core_slots.acquire(timeout=2):
        raise ConfigError("已有两项原生预览或测速运行中，请稍后再试")
    try:
        binary = find_core(Path(root), "mihomo")
        with tempfile.TemporaryDirectory(prefix="yomesh-native-probe-") as temporary, tempfile.TemporaryFile() as logs:
            directory = Path(temporary)
            config = {k: deepcopy(native["mihomo"][k]) for k in ("proxies", "proxy-groups", "proxy-providers", "global-client-fingerprint", "global-ua") if k in native["mihomo"]}
            if group is not None:
                config["proxy-groups"] = [g for g in config.get("proxy-groups", []) if g["name"] != group["name"]] + [deepcopy(group)]
            for g in config.get("proxy-groups", []):
                # Isolated previews must not launch periodic network probes.
                if g.get("type") in {"url-test", "fallback", "load-balance"}:
                    g["lazy"] = True
                    g["interval"] = 0
            for name, p in config.get("proxy-providers", {}).items():
                p["health-check"] = {"enable": False}
                if p.get("type") == "inline":
                    continue
                path = str(p.get("path", "")).removeprefix("./")
                if not path or path not in native.get("documents", {}):
                    raise ConfigError("节点来源缺少缓存，请先刷新该订阅再预览或测速：" + name)
                p["type"] = "file"
                for key in ("url", "proxy", "header", "interval"):
                    p.pop(key, None)
                p["health-check"] = {"enable": False}
            files = {**native.get("documents", {}), **{p: base64.b64decode(v) for p, v in native.get("assets", {}).items()}}
            write_bundle_files(directory, files)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            secret = secrets.token_urlsafe(24)
            config.update({"mixed-port": 0, "allow-lan": False, "external-controller": f"127.0.0.1:{port}", "secret": secret,
                           "log-level": "silent", "mode": "rule", "rules": ["MATCH,DIRECT"], "dns": {"enable": False}})
            write_json(directory / "probe.json", config)
            flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
            proc = subprocess.Popen([str(binary), "-d", str(directory), "-f", str(directory / "probe.json")], cwd=directory, stdout=logs, stderr=logs, **flags)
            def api(path, timeout=5):
                req = Request(f"http://127.0.0.1:{port}" + path, headers={"Authorization": "Bearer " + secret})
                with urlopen(req, timeout=timeout) as reply:
                    return json.load(reply)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise ConfigError("原生内核无法加载当前节点或筛选参数；原配置未改动")
                    try:
                        api("/version", .2)
                        break
                    except OSError:
                        time.sleep(.05)
                else:
                    raise ConfigError("原生预览内核启动超时")
                wait_native_ready(api, config)
                yield api
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
    finally:
        _core_slots.release()


REGIONS = {"JP": "日本|JP|Japan|东京|大阪|🇯🇵", "HK": "香港|HK|Hong.?Kong|🇭🇰", "SG": "新加坡|SG|Singapore|🇸🇬", "US": "美国|US|United.?States|🇺🇸", "TW": "台湾|TW|Taiwan|🇹🇼"}
ADAPTERS = {"ss": "Shadowsocks", "ssr": "ShadowsocksR", "vmess": "Vmess", "vless": "Vless", "trojan": "Trojan", "hysteria": "Hysteria", "hysteria2": "Hysteria2", "wireguard": "WireGuard", "tuic": "Tuic", "socks5": "Socks5", "http": "Http", "snell": "Snell", "ssh": "Ssh", "mieru": "Mieru", "anytls": "AnyTLS", "direct": "Direct", "reject": "Reject"}

# Exact imported forms only. Never guess a region from a partial regex match.
REGION_ALIASES = {
    "JP": ["🇯🇵|JP|JPN|日本|Japan|东京", "日本|JP|Japan"],
    "HK": ["🇭🇰|香港|HongKong|Hong Kong", "🇭🇰|HK|HKG|香港|HongKong|Hong Kong", "香港|HK"],
    "TW": ["🇹🇼|TW|TWN|台湾|臺灣|Taiwan|TaiWan"],
    "US": ["🇺🇸|US|USA|美国|美國|United States|America"],
    "SG": ["🇸🇬|SG|SGP|新加坡|LionCity|Singapore"],
}


def group_filter_view(group):
    if not isinstance(group, dict):
        raise ConfigError("节点组需要对象")
    regex = group.get("filter", "")
    if not isinstance(regex, str):
        raise ConfigError("包含正则需要文本")
    region, residual = "", regex
    for code, pattern in REGIONS.items():
        canonical = "(?i)(?:" + pattern + ")"
        if regex in [canonical, *REGION_ALIASES.get(code, [])]:
            region, residual = code, ""
            break
        prefix = "(?=.*" + canonical + ")(?=.*"
        if regex.startswith(prefix) and regex.endswith(").*"):
            region, residual = code, regex[len(prefix):-3]
            break
    protocol = ""
    raw_protocol = group.get("exclude-type", "")
    if isinstance(raw_protocol, str) and raw_protocol:
        excluded = {v.casefold() for v in raw_protocol.split("|")}
        protocol = next((k for k in ADAPTERS if excluded == {v.casefold() for key, v in ADAPTERS.items() if key != k}), "")
    return {"region": region, "regex": residual, "protocol": protocol,
            "custom_include": bool(residual), "custom_protocol": bool(raw_protocol and not protocol),
            "effective": {key: group.get(key, "") for key in ("filter", "exclude-filter", "exclude-type")},
            "protocols": [{"value": key, "label": value} for key, value in ADAPTERS.items()]}


def group_candidate(native, request):
    group = deepcopy(request.get("group"))
    if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not group["name"].strip():
        raise ConfigError("节点组需要名称")
    filters = request.get("filters")
    if filters is not None:
        if not isinstance(filters, dict):
            raise ConfigError("筛选条件需要对象")
        region, protocol = filters.get("region", ""), filters.get("protocol", "")
        if region not in {"", *REGIONS} or protocol not in {"", *ADAPTERS}:
            raise ConfigError("地区或协议无效")
        regex = filters.get("regex", "")
        excludes = filters.get("exclude", "")
        if not isinstance(regex, str) or not isinstance(excludes, str) or len(regex) > 2048 or len(excludes) > 2048:
            raise ConfigError("筛选文本最多 2048 字符")
        view = group_filter_view(group)
        replace = filters.get("replace_known") is True
        patterns = (["(?i)(?:" + REGIONS[region] + ")"] if region else []) + ([regex] if regex else [])
        # regexp2 lookaheads are supported by the pinned Mihomo group parser.
        if not replace or (region, regex) != (view["region"], view["regex"]):
            group["filter"] = patterns[0] if len(patterns) == 1 else "" if not patterns else "".join("(?=.*" + p + ")" for p in patterns) + ".*"
        terms = [v.strip() for v in re.split("[,，\n]", excludes) if v.strip()]
        if terms:
            extra = "(?i)(?:" + "|".join(re.escape(t) for t in terms) + ")"
            group["exclude-filter"] = "`".join(p for p in (group.get("exclude-filter", ""), extra) if p)
        if protocol:
            group["exclude-type"] = "|".join(value for key, value in ADAPTERS.items() if key != protocol)
        elif replace and view["protocol"]:
            group.pop("exclude-type", None)
    return group


def preview_group(root, native, request):
    group = group_candidate(native, request)
    with native_core(root, native, group) as core:
        result = core("/proxies/" + quote(group["name"], safe=""))
    names = result.get("all", [])
    fallback = names == ["COMPATIBLE"] and "COMPATIBLE" not in group.get("proxies", [])
    if fallback:
        names = []
    catalog = node_catalog(native)
    by_name = {n["name"]: n for n in catalog}
    return {"group": group, "filter_view": group_filter_view(group), "members": [by_name.get(n, {"name": n, "type": "节点组 / 内置出口"}) for n in names], "count": len(names), "fallback": fallback, "method": "mihomo-1.19.32", "note": "未匹配到节点，内核正在使用临时兜底；请调整筛选" if fallback else "根据已缓存节点由原生内核计算；未测速"}


def group_overview(root, native):
    """One isolated core run resolves every group's members for card badges and tests."""
    catalog = node_catalog(native)
    by_name = {n["name"]: n for n in catalog}
    with native_core(root, native) as core:
        proxies = core("/proxies").get("proxies", {})
    groups = {}
    for group in native["mihomo"].get("proxy-groups", []):
        names = (proxies.get(group["name"]) or {}).get("all", [])
        if names == ["COMPATIBLE"] and "COMPATIBLE" not in group.get("proxies", []):
            names = []
        groups[group["name"]] = {"count": len(names), "test_ids": [by_name[name]["id"] for name in names if name in by_name][:300]}
    return {"groups": groups, "method": "mihomo-1.19.32", "note": "根据已缓存节点由原生内核计算；未测速"}


def diagnose(native, query, context=None):
    """Tri-state evaluation: unknown earlier rules prevent a false definitive match."""
    if not isinstance(query, str) or not query.strip() or len(query) > 253:
        raise ConfigError("请输入域名或 IP")
    query = query.strip().lower().rstrip(".")
    context = context or {}
    if not isinstance(context, dict) or set(context) - {"network", "port"}:
        raise ConfigError("推演上下文需要协议与目标端口")
    if context.get("network", "") not in {"", "tcp", "udp"}:
        raise ConfigError("协议请选择 TCP 或 UDP")
    if context.get("port") is not None and context.get("port") != "" and (type(context["port"]) is not int or not 1 <= context["port"] <= 65535):
        raise ConfigError("目标端口需要 1–65535")
    try:
        address = ipaddress.ip_address(query)
    except ValueError:
        check_domain(query, "查询目标")
        address = None
    providers = native["mihomo"].get("rule-providers", {})
    selected = native.get("selection", {}).get("rules")
    from .native_configuration import resolve
    readiness_error = ""
    try:
        _, _, excluded = resolve(native)
        excluded_ids = {p["id"] for p in excluded}
    except ConfigError as error:
        readiness_error = str(error)
        excluded_ids = set()
    def evaluate(expression, trail=()):
        nested = _children(expression)
        if nested:
            op, leaves = nested
            values = [evaluate(v, trail) for v in leaves]
            states = [v[0] for v in values]
            state = (True if True in states else None if None in states else False) if op == "or" else (False if False in states else None if None in states else True)
            return state, ("任意" if op == "or" else "全部") + "条件：" + "；".join(v[1] for v in values[:8])
        if expression.startswith("NOT,(") and expression.endswith(")"):
            state, detail = evaluate(expression[5:-1], trail)
            return None if state is None else not state, "非：" + detail
        kind, _, value = expression.partition(",")
        value = value.removesuffix(",no-resolve")
        if kind in {"MATCH", "FINAL"}:
            return True, "兜底规则"
        if kind == "RULE-SET":
            if value in trail:
                return None, "规则来源存在循环依赖"
            provider = providers.get(value)
            if not provider:
                return None, "缺失规则来源：" + value
            body = native.get("documents", {}).get(str(provider.get("path", "")).removeprefix("./"))
            from .interaction_inspection import rule_entries, entry_expression
            try:
                entries = rule_entries(native, provider)
            except (ConfigError, ValueError, TypeError, yaml.YAMLError):
                entries = None
            if entries is None:
                return None, value + " 无可解析缓存（MRS / 二进制或未刷新），不能确定命中"
            unknown = False
            behavior = provider.get("behavior", "classical")
            for entry in entries:
                if behavior == "domain":
                    if "*" in entry or "?" in entry:
                        unknown = True
                        continue
                    domain = entry.removeprefix("+.").removeprefix("*.").lower().rstrip(".")
                    state = not address and (query == domain or query.endswith("." + domain)) if entry.startswith(("+.", "*.")) else not address and query == domain
                    detail = entry
                elif behavior == "ipcidr":
                    state, detail = evaluate("IP-CIDR," + entry, (*trail, value))
                else:
                    # Classical providers may contain a redundant action field (rules:).
                    leaf = entry_expression(entry, behavior)
                    state, detail = evaluate(leaf, (*trail, value))
                if state is True:
                    return True, value + " → " + detail
                unknown |= state is None
            return (None, value + " 含需运行上下文的条件") if unknown else (False, value + " 未命中")
        if kind in {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD"}:
            if address:
                return False, "IP 查询不匹配域名条件"
            value = value.lower().rstrip(".")
            return (query == value if kind == "DOMAIN" else query == value or query.endswith("." + value) if kind == "DOMAIN-SUFFIX" else value in query), kind + "," + value
        if kind in {"IP-CIDR", "IP-CIDR6"}:
            if address is None:
                return None, "需要 DNS 解析后的 IP，当前未执行 DNS 查询"
            try:
                return address in ipaddress.ip_network(value, strict=False), kind + "," + value
            except ValueError:
                return None, "IP 条件格式错误"
        if kind == "NETWORK" and context.get("network"):
            return context["network"] == value.lower(), "网络协议：" + context["network"].upper()
        if kind == "DST-PORT" and context.get("port"):
            try:
                ranges = [v.split("-") for v in value.split("/")]
                if any(len(v) not in {1, 2} for v in ranges):
                    raise ValueError()
                return any(int(v[0]) <= context["port"] <= int(v[-1]) for v in ranges), "目标端口：" + str(context["port"])
            except ValueError:
                return None, "端口条件需使用客户端上下文解析"
        return None, kind + " 需要协议、端口、进程、Geo 数据或客户端运行上下文"
    path, uncertain = [], False
    missing = []
    duplicates = {}
    for index, row in enumerate(native["policies"], 1):
        expression, target, params = split_target(row["text"])
        refs = re.findall(r"RULE-SET,([^,)]+)", expression)
        missing.extend({"rule_id": row["id"], "rule_name": row.get("name") or expression[:60], "ref": ref, "expression": expression, "target": target} for ref in refs if ref not in providers)
        duplicates.setdefault((expression, tuple(params)), []).append({"id": row["id"], "position": index, "name": row.get("name") or expression[:70], "target": target, "enabled": row["enabled"]})
        if not row["enabled"] or row["id"] in excluded_ids or selected is not None and any(ref not in selected for ref in refs):
            continue
        if path and path[-1]["state"] == "matched":
            continue
        state, detail = evaluate(expression)
        path.append({"id": row["id"], "position": index, "name": row.get("name") or expression[:70], "target": target, "state": "matched" if state is True else "unknown" if state is None else "miss", "detail": detail})
        uncertain |= state is None
    matched = next((r for r in path if r["state"] == "matched"), None)
    return {"query": query, "path": path, "match": matched, "definitive": bool(matched and not uncertain and not readiness_error), "missing": missing, "configuration_error": readiness_error,
            "duplicates": [rows for (expression, _), rows in duplicates.items() if len(rows) > 1 and expression not in {"MATCH", "FINAL"}],
            "note": "配置推演，使用已保存规则缓存；不代表客户端实测。较早的未知条件会阻止确定性结论。"}


def repair_reference(native, request):
    updated = deepcopy(native)
    row = next((p for p in updated["policies"] if p["id"] == request.get("id")), None)
    ref = request.get("ref")
    if not row or not isinstance(ref, str) or ref not in re.findall(r"RULE-SET,([^,)]+)", row["text"]):
        raise ConfigError("规则引用已变化，请重新检查")
    action = request.get("operation")
    if action == "disable":
        row["enabled"] = False
    elif action == "replace":
        replacement = request.get("replacement")
        if replacement not in updated["mihomo"].get("rule-providers", {}):
            raise ConfigError("请选择已有来源")
        row["text"] = re.sub(r"RULE-SET," + re.escape(ref) + r"(?=[,)])", lambda m: "RULE-SET," + replacement, row["text"])
    elif action == "restore":
        if ref in updated["mihomo"].get("rule-providers", {}):
            raise ConfigError("来源已恢复，请重新检查")
        from .native_policy import prepare_request
        updated, _ = prepare_request(updated, {"draft": {"mode": "rules", "name": ref, "target": split_target(row["text"])[1], "rules": []}, "sources": [{"name": ref, "url": request.get("url", "")}]}, fetch=True)
    else:
        raise ConfigError("请选择补回、替换或停用整条规则")
    checked(updated)
    return updated


def parse_import(text):
    """Native YAML/JSON stays lossless; URI conversion rejects unknown parameters."""
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 8 * 1024 * 1024:
        raise ConfigError("导入内容需为 1 字节至 8 MiB")
    text = text.strip()
    if re.fullmatch(r"https?://\S+", text):
        return {"kind": "subscription", "url": text, "nodes": []}
    try:
        import yaml
        from .base_config import UniqueLoader
        value = yaml.load(text, Loader=UniqueLoader)
        json.dumps(value, allow_nan=False)
    except ConfigError:
        raise ConfigError("导入内容含重复或无效映射键，请整理后再导入") from None
    except (ValueError, TypeError, RecursionError):
        raise ConfigError("导入内容含循环引用或不支持的数值 / 日期类型") from None
    except Exception:
        value = None
    if isinstance(value, dict) and any(k in value for k in ("rules", "proxy-groups", "proxy-providers", "rule-providers", "dns", "listeners")):
        if "rules" not in value or not isinstance(value["rules"], list) or not value["rules"]:
            raise ConfigError("完整配置必须包含非空 rules 列表；不会丢弃其他字段改当单个节点")
        native = {"format": "mihomo-v1", "mihomo": deepcopy(value), "policies": [{"id": "np_" + uuid.uuid4().hex, "text": line, "enabled": True} for line in value["rules"]], "documents": {}, "assets": {}}
        native["mihomo"].pop("rules")
        if isinstance(native["mihomo"].get("external-ui"), str) and native["mihomo"]["external-ui"].strip():
            from .native_configuration import normalize_ui_path
            native["mihomo"]["external-ui"] = normalize_ui_path(native["mihomo"]["external-ui"])
        checked(native)
        return {"kind": "configuration", "configuration": native, "nodes": []}
    if isinstance(value, dict) and isinstance(value.get("proxies"), list):
        if set(value) != {"proxies"}:
            raise ConfigError("节点列表含其他顶层字段，请使用完整配置导入")
        nodes = value["proxies"]
    elif isinstance(value, list):
        nodes = value
    elif isinstance(value, dict) and "type" in value:
        nodes = [value]
    else:
        from .subscriptions import uri_node, normalize
        from .compiler import node_mihomo
        try:
            decoded = base64.b64decode(text + "=" * (-len(text) % 4)).decode()
            if "://" in decoded:
                text = decoded
        except (ValueError, UnicodeError):
            pass
        nodes = []
        allowed = {"security", "sni", "peer", "insecure", "allowInsecure", "fp", "alpn", "pbk", "sid", "flow", "type", "path", "host", "serviceName", "obfs", "obfs-password", "encryption"}
        from urllib.parse import parse_qs
        for index, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            query = parse_qs(urlsplit(line).query, keep_blank_values=True)
            scheme = urlsplit(line).scheme.lower()
            common_tls = {"sni", "peer", "insecure", "allowInsecure", "alpn"}
            protocol_allowed = {"ss": set(), "trojan": common_tls | {"security", "fp", "type", "path", "host", "serviceName"},
                                "hy2": common_tls | {"obfs", "obfs-password"}, "hysteria2": common_tls | {"obfs", "obfs-password"},
                                "vmess": common_tls | {"security", "fp", "type", "path", "host", "serviceName"},
                                "vless": common_tls | {"security", "fp", "pbk", "sid", "flow", "type", "path", "host", "serviceName", "encryption"}}.get(scheme, allowed)
            if set(query) - protocol_allowed or any(len(v) != 1 for v in query.values()):
                raise ConfigError(f"第 {index} 行含不能无损转换的参数，请改用原生 YAML / JSON 导入")
            if line.startswith("vmess://") and "@" not in line.split("#", 1)[0]:
                from .subscriptions import decode64
                try:
                    vmess = json.loads(decode64(line[8:].split("#", 1)[0]))
                    if set(vmess) - {"v", "ps", "add", "port", "id", "aid", "scy", "tls", "sni", "host", "net", "path"}:
                        raise ValueError()
                except Exception:
                    raise ConfigError(f"第 {index} 行 VMess 含不能无损转换的字段，请改用原生 YAML / JSON") from None
            try:
                parsed = normalize(uri_node(line), "manual")
                nodes.append(node_mihomo(parsed, parsed["name"]))
            except Exception:
                raise ConfigError(f"第 {index} 行节点不能完整转换，请核对格式或改用原生 YAML / JSON") from None
    if not nodes or len(nodes) > 1000:
        raise ConfigError("一次可导入 1–1000 个节点")
    for index, node in enumerate(nodes, 1):
        if not isinstance(node, dict) or not isinstance(node.get("name"), str) or not node["name"].strip() or not isinstance(node.get("type"), str):
            raise ConfigError(f"第 {index} 项需要节点名称与协议")
    return {"kind": "nodes", "nodes": deepcopy(nodes)}


def prepare_import(native, request, fetch=False):
    parsed = parse_import(request.get("text"))
    if parsed["kind"] == "subscription":
        if not fetch:
            return {"kind": "subscription", "items": [], "note": "订阅 URL 尚未拉取；点击读取后查看实际节点"}
        from .native_configuration import refresh_provider
        name = request.get("name", "").strip() or "新订阅"
        if name in native["mihomo"].get("proxy-providers", {}):
            raise ConfigError("同名订阅已存在，请修改名称")
        candidate = deepcopy(native)
        candidate["mihomo"].setdefault("proxy-providers", {})[name] = {"type": "http", "url": parsed["url"], "path": "providers/import-" + uuid.uuid4().hex + ".yaml", "interval": 86400, "health-check": {"enable": False}}
        candidate = refresh_provider(candidate, "proxy-providers", name)
        return {"kind": "subscription", "configuration": candidate, "provider": name, "items": [{"name": n["name"], "type": n["type"], "duplicate": False} for n in node_catalog(candidate) if n["provider"] == name]}
    if parsed["kind"] == "configuration":
        return {**parsed, "items": [], "note": "将创建独立空间；保留所有原生字段。缺失本地缓存时需补齐再原生校验导出。"}
    names = {p["name"] for p in native["mihomo"].get("proxies", [])}
    fingerprints = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in native["mihomo"].get("proxies", [])}
    items = []
    for node in parsed["nodes"]:
        fingerprint = json.dumps(node, sort_keys=True, ensure_ascii=False)
        duplicate = node["name"] in names or fingerprint in fingerprints
        items.append({"name": node["name"], "type": node["type"], "duplicate": duplicate})
        names.add(node["name"])
        fingerprints.add(fingerprint)
    return {**parsed, "items": items}
