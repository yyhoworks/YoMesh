"""Lossless, explicitly Mihomo/LocalDNS configuration spaces.

These documents remain authoritative native configuration inside Profile. They
are not translated through the portable subset or silently converted to MosDNS.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections import OrderedDict
from copy import deepcopy
from pathlib import PurePosixPath

import yaml

from .model import ConfigError
from .netguard import guarded_fetch

FORMAT = "mihomo-localdns-v1"
PROXY_FORMAT = "mihomo-v1"
LOCALDNS_VERSION = "FJGAC"
LOCALDNS_SHA256 = "a4ad9e136f19d01bae2ecc1b1de80337f8f3e491db8e6254469003755d1d7a98"


def load_yaml(text):
    return yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))


_rule_yaml_cache = OrderedDict()
_rule_yaml_cache_lock = threading.Lock()


def rule_yaml_entries(body):
    """Read either native YAML list spelling without rewriting the source."""
    try:
        parsed = load_yaml(body)
        if not isinstance(parsed, dict):
            raise ValueError()
        keys = [key for key in ("payload", "rules") if key in parsed]
        if len(keys) != 1:
            raise ValueError()
        entries = parsed[keys[0]]
        if not isinstance(entries, list) or any(not isinstance(line, str) for line in entries):
            raise ValueError()
        return entries
    except (yaml.YAMLError, AttributeError, TypeError, ValueError):
        raise ConfigError("规则 YAML 需要 payload 或 rules 字符串列表，且不能同时声明两者；原内容保留") from None


def validate_rule_yaml(body):
    """Cache only content digests and dependency names, never old rule bodies."""
    digest = hashlib.sha256(body.encode()).digest()
    with _rule_yaml_cache_lock:
        if digest in _rule_yaml_cache:
            _rule_yaml_cache.move_to_end(digest)
            return _rule_yaml_cache[digest]
    payload = rule_yaml_entries(body)
    refs = tuple(sorted({ref for line in payload for ref in re.findall(r"RULE-SET,([^,)]+)", line)}))
    with _rule_yaml_cache_lock:
        _rule_yaml_cache[digest] = refs
        _rule_yaml_cache.move_to_end(digest)
        while len(_rule_yaml_cache) > 128:
            _rule_yaml_cache.popitem(last=False)
    return refs


def safe_path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ConfigError("原生配置资产路径无效")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in {"..", "."} for p in value.split("/")):
        raise ConfigError("原生配置资产必须使用包内相对路径")
    if value in {"mihomo.yaml", "manifest.json", "README.txt"}:
        raise ConfigError("原生资产与生成文件重名")
    return value


def json_document(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ConfigError("JSON 包含重复字段")
            result[key] = value
        return result
    try:
        return json.loads(text, object_pairs_hook=pairs)
    except (ValueError, TypeError):
        raise ConfigError("原生配置文件不是有效 JSON") from None


def checked(value):
    if not isinstance(value, dict) or value.get("format") not in (FORMAT, PROXY_FORMAT):
        raise ConfigError("原生配置格式不受支持")
    if set(value) - {"format", "mihomo", "policies", "documents", "assets", "selection", "origin", "group_roles", "rule_source_adapters"}:
        raise ConfigError("原生配置含未知管理字段")
    config = value.get("mihomo")
    if not isinstance(config, dict):
        raise ConfigError("Mihomo 配置需要对象")
    # Do not discard any native fields. Target core performs final validation.
    for key in ("proxy-groups", "proxies", "listeners"):
        rows = config.get(key, [])
        if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
            raise ConfigError("Mihomo " + key + " 需要对象列表")
        names = [r.get("name") for r in rows]
        if any(not isinstance(n, str) or not n.strip() for n in names) or len(names) != len(set(names)):
            raise ConfigError("Mihomo " + key + " 名称不能为空或重复")
    for key in ("proxy-providers", "rule-providers"):
        if not isinstance(config.get(key, {}), dict) or any(not isinstance(r, dict) for r in config.get(key, {}).values()):
            raise ConfigError("Mihomo " + key + " 需要名称到对象的映射")
    roles = value.get("group_roles", {})
    from .native_rule_sources import validate_adapters
    validate_adapters(value)
    names = {g["name"] for g in config.get("proxy-groups", [])}
    if not isinstance(roles, dict) or any(name not in names or not isinstance(role, str) or role not in {"nodes", "rules", "shared"} for name, role in roles.items()):
        raise ConfigError("节点组管理位置无效，需要已存组名和 nodes / rules / shared")
    policies = value.get("policies")
    if not isinstance(policies, list) or not policies:
        raise ConfigError("原生配置至少需要一条分流规则")
    ids = []
    for row in policies:
        if not isinstance(row, dict) or not {"id", "text", "enabled"} <= set(row) or set(row) - {"id", "text", "enabled", "name"}:
            raise ConfigError("原生分流规则字段无效")
        if "name" in row and (not isinstance(row["name"], str) or len(row["name"]) > 80):
            raise ConfigError("策略名称最多 80 个字符")
        if not isinstance(row["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["id"]):
            raise ConfigError("原生分流规则 ID 无效")
        if not isinstance(row["text"], str) or not row["text"].strip() or "\n" in row["text"] or type(row["enabled"]) is not bool:
            raise ConfigError("原生分流规则需要单行文本和启用状态")
        ids.append(row["id"])
    if len(ids) != len(set(ids)):
        raise ConfigError("原生分流规则 ID 重复")
    docs, assets = value.get("documents", {}), value.get("assets", {})
    if not isinstance(docs, dict) or not isinstance(assets, dict) or set(docs) & set(assets):
        raise ConfigError("原生文件清单无效或重复")
    size = 0
    for path, body in docs.items():
        safe_path(path)
        if not isinstance(body, str):
            raise ConfigError("文本资产需要字符串")
        size += len(body.encode())
        if path.endswith(".json"):
            json_document(body)
    for path, body in assets.items():
        safe_path(path)
        try:
            size += len(base64.b64decode(body, validate=True))
        except (ValueError, TypeError):
            raise ConfigError("二进制资产需要有效 Base64") from None
    if size > 16 * 1024 * 1024:
        raise ConfigError("原生配置资产最多 16 MiB")
    if value["format"] == FORMAT and "localdns/config/localdns.json" not in docs:
        raise ConfigError("缺少 LocalDNS 主配置")
    selection = value.get("selection", {})
    if not isinstance(selection, dict) or set(selection) - {"groups", "rules"}:
        raise ConfigError("原生配置选用字段无效")
    for key, names in (("groups", [g["name"] for g in config.get("proxy-groups", [])]), ("rules", list(config.get("rule-providers", {})))):
        chosen = selection.get(key)
        if chosen is not None and (not isinstance(chosen, list) or any(not isinstance(i, str) for i in chosen) or len(set(chosen)) != len(chosen) or set(chosen)-set(names)):
            raise ConfigError("原生配置选用包含重复或不存在的资源")
    # Reject malformed cache edits before persistence and overview rendering.
    cached_proxy_names(value)
    for provider in config.get("rule-providers", {}).values():
        if provider.get("format", "yaml") != "yaml":
            continue
        body = docs.get(str(provider.get("path", "")).removeprefix("./"))
        if body:
            validate_rule_yaml(body)
    return deepcopy(value)


def resolve(value):
    native = checked(value)
    config = deepcopy(native["mihomo"])
    selection = native.get("selection", {})
    all_groups = {g["name"]: g for g in config.get("proxy-groups", [])}
    chosen = set(selection.get("groups") if selection.get("groups") is not None else all_groups)
    def children(name, trail=()):
        if name in trail:
            raise ConfigError("原生节点组存在循环引用")
        for member in all_groups[name].get("proxies", []):
            if member in all_groups:
                chosen.add(member)
                children(member, (*trail, name))
    for name in list(chosen):
        children(name)
    selected_rules = set(selection.get("rules") if selection.get("rules") is not None else config.get("rule-providers", {}))
    providers = config.get("rule-providers", {})
    reference_cache = {}
    def provider_refs(name, trail=()):
        if name in reference_cache:
            return reference_cache[name]
        if name in trail:
            raise ConfigError("规则订阅存在循环引用")
        provider = providers.get(name)
        if provider is None:
            raise ConfigError("规则内容引用了不存在的规则订阅：" + name)
        payload = provider.get("payload")
        dependencies = None
        if payload is None and provider.get("behavior") == "classical":
            path = provider.get("path", "").removeprefix("./")
            body = native.get("documents", {}).get(path)
            if body is None and path in native.get("assets", {}):
                try:
                    body = base64.b64decode(native["assets"][path], validate=True).decode("utf-8-sig")
                except (UnicodeError, ValueError):
                    raise ConfigError("声明为 YAML 的规则缓存不是有效 UTF-8 文本，原始文件已保留") from None
            if body:
                dependencies = validate_rule_yaml(body)
        result = {name}
        if dependencies is None:
            dependencies = {child for line in payload or [] for child in re.findall(r"RULE-SET,([^,)]+)", str(line))}
        for child in dependencies:
            result.update(provider_refs(child, (*trail, name)))
        reference_cache[name] = result
        return result
    excluded, active, used_rules = [], [], set()
    for policy in native["policies"]:
        if not policy["enabled"]:
            continue
        refs = set(re.findall(r"RULE-SET,([^,)]+)", policy["text"]))
        if refs - set(providers):
            raise ConfigError("分流策略引用了不存在的规则订阅：" + ", ".join(sorted(refs - set(providers))))
        if refs - selected_rules:
            excluded.append({"id": policy["id"], "name": policy["text"].split(",")[0], "reason": "引用了停用的规则订阅，整条策略跳过", "rules": sorted(refs - selected_rules)})
            continue
        refs = set().union(*(provider_refs(name) for name in refs)) if refs else set()
        missing = refs - selected_rules
        if missing:
            excluded.append({"id": policy["id"], "name": policy["text"].split(",")[0], "reason": "引用了停用的规则订阅，整条策略跳过", "rules": sorted(missing)})
            continue
        active.append(policy["text"])
        used_rules.update(refs)
    if not active or not active[-1].startswith(("MATCH,", "FINAL,")):
        raise ConfigError("原生策略需要最后一条启用的 MATCH / FINAL 兜底")
    config["rules"] = active
    config["rule-providers"] = {name: provider for name, provider in providers.items() if name in used_rules}
    config["proxy-groups"] = [g for g in config.get("proxy-groups", []) if g["name"] in chosen]
    known = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", *chosen, *(n["name"] for n in config.get("proxies", []))}
    for rule in active:
        parts = rule.rsplit(",", 2)
        target = parts[-2] if parts[-1] in {"no-resolve", "src", "dst"} else parts[-1]
        if target not in known:
            raise ConfigError("分流策略引用了未选用或不存在的出口")
    for group in config["proxy-groups"]:
        if any(p not in config.get("proxy-providers", {}) for p in group.get("use", [])):
            raise ConfigError("节点组引用了不存在的节点订阅")
        if any(p not in known for p in group.get("proxies", [])):
            # Provider members can also be selected explicitly by name.
            cached_names = cached_proxy_names(native)
            if any(p not in known and p not in cached_names for p in group.get("proxies", [])):
                raise ConfigError("节点组引用了不存在的成员")
    return native, config, excluded


def cached_proxy_names(native):
    result = set()
    for provider in native["mihomo"].get("proxy-providers", {}).values():
        if provider.get("type") == "inline":
            rows = provider.get("payload", [])
            if not isinstance(rows, list) or any(not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"] for p in rows):
                raise ConfigError("内联节点来源格式无效")
            result.update(p["name"] for p in rows)
            continue
        path = provider.get("path", "").removeprefix("./")
        body = native.get("documents", {}).get(path)
        if body:
            try:
                rows = load_yaml(body).get("proxies")
                if not isinstance(rows, list) or any(not isinstance(p, dict) or not isinstance(p.get("name"), str) or not p["name"] for p in rows):
                    raise ValueError()
                result.update(p["name"] for p in rows)
            except (yaml.YAMLError, TypeError, KeyError, AttributeError, ValueError):
                raise ConfigError("节点订阅缓存格式无效") from None
    return result


def overview(value):
    from .interaction_tools import node_catalog
    result = node_catalog(value)
    from .native_policy import overview as policy_overview
    return {"nodes": result, "dns_engine": "localdns" if value["format"] == FORMAT else None, **policy_overview(value)}


def summary(value):
    native = checked(value)
    dns_engine = "localdns" if native["format"] == FORMAT else None
    config = native["mihomo"]
    counts = {"sources": len(config.get("proxy-providers", {})), "nodes": len(cached_proxy_names(native) | {p["name"] for p in config.get("proxies", [])}), "groups": len(config.get("proxy-groups", [])), "library": len(config.get("rule-providers", {})), "policies": len(native["policies"]), "inbounds": len(config.get("listeners", []))}
    from .native_rule_groups import overview as group_overview
    group_view = group_overview(native)
    counts.update(groups=len(group_view["node_groups"]), library=sum(not g["fallback"] for g in group_view["rule_groups"]), policies=len(group_view["rule_groups"]))
    try:
        _, selected, excluded = resolve(native)
        raw_counts = {**counts, "groups": len(selected.get("proxy-groups", [])), "library": len(selected.get("rule-providers", {})), "policies": len(selected["rules"])}
        selected_groups = {g["name"] for g in selected.get("proxy-groups", [])}
        skipped = {p["id"] for p in excluded}
        active_units = [g for g in group_view["rule_groups"] if any(p["enabled"] and p["id"] not in skipped for p in g["parts"])]
        counts.update(groups=len(set(group_view["node_groups"]) & selected_groups), library=sum(not g["fallback"] for g in active_units), policies=len(active_units))
        return {"valid": True, "error": "", "counts": counts, "native_counts": raw_counts, "excluded_policies": excluded, "native": True, "dns_engine": dns_engine}
    except ConfigError as error:
        return {"valid": False, "error": str(error), "counts": counts, "excluded_policies": [], "native": True, "dns_engine": dns_engine}


def bundle(value, proxy, rule_output, component="both"):
    if value.get("format") == PROXY_FORMAT:
        if component == "dns":
            raise ConfigError("此空间只包含 Mihomo 代理配置；内置 DNS 在基础设置中编辑，没有独立 DNS 配置可导出")
        component = "proxy"
    if proxy != "mihomo" and component != "dns":
        raise ConfigError("此空间保留 Mihomo / LocalDNS 原生语义，不能直接转成 sing-box 或 MosDNS")
    if rule_output != "remote" and component != "dns":
        raise ConfigError("原生空间使用原有订阅引用和包内缓存，请选择原生输出")
    if component == "dns":
        native = checked(value)
        files = {path: body for path, body in native.get("documents", {}).items() if path.startswith("localdns/")}
        files.update({path: base64.b64decode(body, validate=True) for path, body in native.get("assets", {}).items() if path.startswith("localdns/")})
        files["README.txt"] = "LocalDNS FJGAC 独立配置（不是 MosDNS）。\n保留 localdns/ 目录，执行 localdns --root ./localdns --config ./localdns/config/localdns.json\n上游与 SOCKS 接线保留当前值，需目标设备可达。此包不包含 Mihomo；导出不启动服务。\n"
        return {"files": files, "manifest": {"format": "yomesh-bundle-v1", "component": "dns", "proxy": "mihomo", "dns_engine": "localdns", "versions": {"localdns": LOCALDNS_VERSION}, "localdns_sha256": LOCALDNS_SHA256, "rule_output": {"mode": "preserve-native"}, "validation": {"status": "not-run"}, "startup_order": ["localdns"], "native_space": True}}
    native, config, excluded = resolve(value)
    from .native_rule_sources import export_adapters
    adapted_sources = export_adapters(native, config)
    files = dict(native.get("documents", {}))
    files.update({path: base64.b64decode(body, validate=True) for path, body in native.get("assets", {}).items()})
    # Bundle paths are portable; absolute filesystem references are never opened.
    for section in ("proxy-providers", "rule-providers"):
        for provider in config.get(section, {}).values():
            path = provider.get("path")
            if path:
                clean = path.removeprefix("./")
                safe_path(clean)
                if clean not in files:
                    raise ConfigError("缺少订阅缓存文件；请在来源或规则页面导入内容")
    for listener in config.get("listeners", []):
        for field in ("certificate", "private-key"):
            path = listener.get(field)
            if isinstance(path, str) and "\n" not in path:
                safe_path(path.removeprefix("./"))
                if path.removeprefix("./") not in files:
                    raise ConfigError("缺少入站证书或私钥资产")
    files["mihomo.yaml"] = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    files["README.txt"] = "Mihomo 1.19.32 + LocalDNS FJGAC 原生配置。\n保留 providers、ruleset、certs、localdns 目录。\nLocalDNS：localdns --root ./localdns --config ./localdns/config/localdns.json\nMihomo：mihomo -d . -f ./mihomo.yaml\n监听地址、透明代理、ECS 和外部 API 仍按原配置，请在目标设备部署。\nUI 静态资源不属于配置；external-ui 指向目标设备已有的 ui 目录。\n导出不启动服务或修改路由。\n"
    manifest = {"format": "yomesh-bundle-v1", "component": component, "proxy": "mihomo", "dns_engine": "localdns", "versions": {"mihomo": "1.19.32", "localdns": LOCALDNS_VERSION}, "localdns_sha256": LOCALDNS_SHA256, "rule_output": {"mode": "preserve-native", "notes": ["保留原订阅地址、格式、缓存与复杂匹配，不转换规则语义"]}, "composition": {"excluded_policies": excluded}, "repair": deepcopy(native.get("origin", {}).get("repair")), "validation": {"status": "not-run"}, "startup_order": ["localdns", "mihomo"], "native_space": True}
    if adapted_sources:
        manifest["rule_output"]["adapters"] = adapted_sources
        manifest["rule_output"]["notes"] = ["完整规则列表按显式设置转为本地规则快照，匹配条件和解析参数保留，出口由当前规则组统一指定；在 YoMesh 刷新后重新导出"]
    if component == "proxy":
        # Keep an asset under localdns/ if the proxy explicitly references it.
        required = {str(provider.get("path", "")).removeprefix("./") for section in ("proxy-providers", "rule-providers") for provider in config.get(section, {}).values()}
        required.update(str(listener.get(field, "")).removeprefix("./") for listener in config.get("listeners", []) for field in ("certificate", "private-key"))
        files = {path: body for path, body in files.items() if not path.startswith("localdns/") or path in required}
        manifest["versions"] = {"mihomo": "1.19.32"}
        manifest["startup_order"] = ["mihomo"]
        manifest.pop("dns_engine")
        manifest.pop("localdns_sha256")
        files["README.txt"] = "Mihomo 1.19.32 独立配置。\n保留 providers、ruleset、certs 等资产目录，执行 mihomo -d . -f ./mihomo.yaml\nDNS、透明代理和外部 API 按基础配置保留。需要 LocalDNS 时请在独立 DNS 区导出并部署。\nUI 静态资源不属于配置包。导出不启动服务或修改路由。\n"
    return {"files": files, "manifest": manifest}


def refresh_provider(value, section, name):
    native = checked(value)
    if section not in {"proxy-providers", "rule-providers"}:
        raise ConfigError("请选择节点订阅或规则订阅")
    provider = native["mihomo"].get(section, {}).get(name)
    if not provider or provider.get("type") != "http" or not str(provider.get("url", "")).startswith(("https://", "http://")):
        raise ConfigError("所选来源不是 HTTP 订阅")
    path = safe_path(provider.get("path", "").removeprefix("./"))
    headers = {}
    for key, val in provider.get("header", {}).items():
        headers[key] = val[0] if isinstance(val, list) else val
    try:
        raw, _ = guarded_fetch(
            provider["url"],
            timeout=25,
            max_bytes=8 * 1024 * 1024,
            headers=headers,
            allow_private=provider.get("allow_private", False),
            what="规则上游地址",
        )
        if len(raw) > 8 * 1024 * 1024:
            raise ConfigError("订阅内容超过 8 MiB")
        if path.endswith((".mrs", ".srs")):
            native.setdefault("assets", {})[path] = base64.b64encode(raw).decode()
            native.get("documents", {}).pop(path, None)
        else:
            text = raw.decode("utf-8-sig")
            parsed = yaml.safe_load(text)
            if section == "rule-providers" and name in native.get("rule_source_adapters", {}):
                from .native_rule_sources import cache_rule_list
                text = cache_rule_list(native, name, text)
                provider["behavior"] = "classical"
            else:
                if section == "rule-providers":
                    rule_yaml_entries(text)
                elif not isinstance(parsed, dict) or not isinstance(parsed.get("proxies"), list):
                    raise ConfigError("订阅内容格式与来源不符，保留原缓存")
            native.setdefault("documents", {})[path] = text
            native.get("assets", {}).pop(path, None)
    except (OSError, ValueError, yaml.YAMLError):
        raise ConfigError("订阅拉取或解析失败，原内容保留") from None
    return checked(native)
