"""Human-facing policy editing; native rule text remains the compiler input."""
from __future__ import annotations

import ipaddress
import re
import uuid
from copy import deepcopy
from urllib.parse import urlparse

from .model import ConfigError

MATCHERS = {"DOMAIN-SUFFIX": "域名后缀", "DOMAIN": "完整域名", "DOMAIN-KEYWORD": "域名关键词", "IP-CIDR": "IPv4 网段", "IP-CIDR6": "IPv6 网段", "SRC-IP-CIDR": "来源 IP 网段", "NETWORK": "网络协议", "DST-PORT": "目标端口", "SRC-PORT": "来源端口", "IN-NAME": "入站名称"}
PARAMS = {"no-resolve", "src", "dst"}


def split_target(text):
    """Split only the action suffix, leaving nested/unknown conditions intact."""
    parts = text.split(",")
    params = []
    while parts and parts[-1] in PARAMS:
        params.insert(0, parts.pop())
    if len(parts) < 2 or not parts[-1]:
        raise ConfigError("策略需要完整的匹配条件与出口")
    return ",".join(parts[:-1]), parts[-1], params


def is_fallback(text):
    return text.startswith(("MATCH,", "FINAL,"))


def _children(expression):
    operator, _, payload = expression.partition(",")
    if operator not in {"OR", "AND"} or not payload.startswith("(") or not payload.endswith(")"):
        return None
    inner, depth, start, rows = payload[1:-1], 0, None, []
    for index, char in enumerate(inner):
        if char == "(":
            if depth == 0:
                start = index + 1
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
            if depth == 0:
                rows.append(inner[start:index])
        elif depth == 0 and char != ",":
            return None
    return (operator.lower(), rows) if depth == 0 and rows else None


def decode(text):
    expression, target, params = split_target(text)
    if is_fallback(text):
        return {"mode": "fallback", "target": target, "text": text}
    nested = _children(expression)
    operator, leaves = nested or ("or", [expression])
    atoms = [leaf.split(",") for leaf in leaves]
    # Forms cover only uniform, non-nested leaves. Mixed/native conditions stay raw.
    if (not nested or not params) and all(len(a) in {2, 3} and (len(a) == 2 or a[2] == "no-resolve") for a in atoms):
        types = {a[0] for a in atoms}
        flags = {len(a) == 3 for a in atoms} if nested else {params == ["no-resolve"]}
        if len(types) == 1 and len(flags) == 1 and (not params or params == ["no-resolve"]):
            kind = atoms[0][0]
            common = {"operator": operator, "no_resolve": flags.pop(), "target": target}
            if kind == "RULE-SET":
                return {"mode": "rules", "rules": [a[1] for a in atoms], **common}
            if kind in MATCHERS:
                return {"mode": "matcher", "matcher_type": kind, "values": [a[1] for a in atoms], **common}
    return {"mode": "advanced", "text": text, "target": target}


def targets(native):
    groups = {g["name"]: g for g in native["mihomo"].get("proxy-groups", [])}
    chosen = set(native.get("selection", {}).get("groups") or []) if native.get("selection", {}).get("groups") is not None else set(groups)
    def include(name, trail=()):
        if name in trail:
            return
        for child in groups.get(name, {}).get("proxies", []):
            if child in groups:
                chosen.add(child); include(child, (*trail, name))
    for name in list(chosen):
        include(name)
    def direct(name, trail=()):
        if name == "DIRECT":
            return True
        group = groups.get(name)
        return bool(group and name not in trail and not group.get("use") and not group.get("include-all") and not group.get("include-all-providers") and not group.get("include-all-proxies") and group.get("proxies") and all(direct(p, (*trail, name)) for p in group["proxies"]))
    result = [{"value": "DIRECT", "label": "直连", "kind": "direct", "available": True}, {"value": "REJECT", "label": "拦截", "kind": "reject", "available": True}, {"value": "REJECT-DROP", "label": "静默丢弃", "kind": "reject", "available": True}]
    result.extend({"value": name, "label": ("直连 · " if direct(name) else "节点组 · ") + name, "kind": "direct" if direct(name) else "proxy", "available": name in chosen} for name in groups)
    result.extend({"value": node["name"], "label": "节点 · " + node["name"], "kind": "proxy", "available": True} for node in native["mihomo"].get("proxies", []))
    return result


def row_view(native, row, target_rows=None):
    try:
        spec = decode(row["text"])
    except ConfigError:
        spec = {"mode": "advanced", "text": row["text"], "target": ""}
    refs = list(dict.fromkeys(re.findall(r"RULE-SET,([^,)]+)", row["text"])))
    providers = native["mihomo"].get("rule-providers", {})
    mode = spec["mode"]
    if mode == "rules":
        summary = ("任意命中：" if spec["operator"] == "or" else "同时命中：") + "、".join(spec["rules"]) if len(spec["rules"]) > 1 else "规则 · " + spec["rules"][0]
        name = " + ".join(spec["rules"])
    elif mode == "matcher":
        summary = MATCHERS[spec["matcher_type"]] + " · " + "、".join(spec["values"])
        name = MATCHERS[spec["matcher_type"]]
    elif mode == "fallback":
        summary, name = "未命中前面策略的流量", "兜底策略"
    else:
        summary, name = "组合匹配 · 条件保持完整", "组合策略"
    target = next((t for t in (target_rows or targets(native)) if t["value"] == spec["target"]), None)
    selected = native.get("selection", {}).get("rules")
    unavailable = [r for r in refs if r not in providers or selected is not None and r not in selected]
    return {"id": row["id"], "name": row.get("name") or name, "custom_name": row.get("name", ""), "summary": summary, "target": spec["target"], "kind": target["kind"] if target else "other", "enabled": row["enabled"], "fallback": mode == "fallback", "editor": spec, "unavailable_rules": unavailable, "sources": [{"name": name, "url": providers.get(name, {}).get("url", "")} for name in refs]}


def overview(native):
    outbounds = targets(native)
    from .native_rule_groups import overview as group_overview
    return {"policies": [row_view(native, p, outbounds) for p in native["policies"]], "targets": outbounds, "matchers": [{"value": key, "label": label} for key, label in MATCHERS.items()], "rule_choices": [{"name": name, "type": p.get("type", "http"), "selected": native.get("selection", {}).get("rules") is None or name in native["selection"]["rules"]} for name, p in native["mihomo"].get("rule-providers", {}).items()], **group_overview(native)}


def _validate_target(native, target, original=None):
    option = next((t for t in targets(native) if t["value"] == target), None)
    if not option or not option["available"]:
        # Existing advanced exits may be preserved by a metadata-only save.
        if original and split_target(original["text"])[1] == target:
            return
        raise ConfigError("请选择已在此方案选用的节点组、节点、直连或拦截")


def _validate_value(kind, value):
    if not value or "," in value or "\n" in value:
        raise ConfigError("匹配值不能为空，也不能包含逗号或换行")
    if kind in {"IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR"}:
        try:
            network = ipaddress.ip_network(value, strict=False)
            if kind != "SRC-IP-CIDR" and network.version != (6 if kind == "IP-CIDR6" else 4):
                raise ValueError()
        except ValueError:
            raise ConfigError("请填写正确的 IP 网段，如 192.168.0.0/16") from None
    if kind in {"DST-PORT", "SRC-PORT"}:
        for item in value.split("/"):
            pieces = item.split("-")
            if not 1 <= len(pieces) <= 2 or any(not x.isdigit() or not 1 <= int(x) <= 65535 for x in pieces) or len(pieces) == 2 and int(pieces[0]) > int(pieces[1]):
                raise ConfigError("端口需为 1–65535，可填写范围或用 / 分隔")
    if kind == "NETWORK" and value not in {"tcp", "udp"}:
        raise ConfigError("网络协议请选择 tcp 或 udp")


def prepare(native, draft, ident=None):
    if not isinstance(draft, dict):
        raise ConfigError("策略表单无效")
    original = next((p for p in native["policies"] if p["id"] == ident), None)
    if ident and not original:
        raise ConfigError("策略已删除，请刷新后重试")
    name, mode = draft.get("name", ""), draft.get("mode")
    if not isinstance(name, str) or len(name.strip()) > 80:
        raise ConfigError("策略名称最多 80 个字符")
    enabled = draft.get("enabled", original["enabled"] if original else True)
    if type(enabled) is not bool:
        raise ConfigError("策略启用状态无效")
    if mode == "advanced":
        text = draft.get("text", "").strip()
        if not text or "\n" in text or "\r" in text:
            raise ConfigError("完整表达式需为一行")
        target = split_target(text)[1]
        if draft.get("set_target") is True:
            expression, _, params = split_target(text)
            target = draft.get("target")
            text = ",".join([expression, target, *params])
    else:
        target, operator, no_resolve = draft.get("target"), draft.get("operator", "or"), draft.get("no_resolve", False)
        if operator not in {"or", "and"} or type(no_resolve) is not bool:
            raise ConfigError("匹配关系或解析选项无效")
        if mode == "fallback":
            if not original or not is_fallback(original["text"]):
                raise ConfigError("兜底由现有最后一条策略编辑")
            text = original["text"].split(",", 1)[0] + "," + str(target)
        else:
            if mode == "rules":
                values, kind = draft.get("rules"), "RULE-SET"
            elif mode == "matcher" and draft.get("matcher_type") in MATCHERS:
                values, kind = draft.get("values"), draft["matcher_type"]
            else:
                raise ConfigError("请选择已存规则或匹配器")
            if not isinstance(values, list) or not values or len(values) > 128 or any(not isinstance(v, str) for v in values):
                raise ConfigError("请至少选择一条规则或填写一个匹配值，最多 128 条")
            for value in values:
                _validate_value(kind, value)
                if kind == "RULE-SET" and value not in native["mihomo"].get("rule-providers", {}):
                    raise ConfigError("选中的规则已不存在，请重新选择")
            if len(set(values)) != len(values):
                raise ConfigError("匹配内容不能重复")
            suffix = ",no-resolve" if no_resolve else ""
            if len(values) == 1:
                text = f"{kind},{values[0]},{target}{suffix}"
            else:
                expression = operator.upper() + ",(" + ",".join("(" + kind + "," + value + suffix + ")" for value in values) + ")"
                text = expression + "," + str(target)
        # Opening a form and saving must not normalize the existing expression.
        if original:
            old = decode(original["text"])
            comparable = {k: draft.get(k, old.get(k)) for k in old if k not in {"target", "text"}}
            if comparable == {k: v for k, v in old.items() if k not in {"target", "text"}}:
                expression, previous, params = split_target(original["text"])
                text = original["text"] if target == previous else ",".join([expression, target, *params])
    _validate_target(native, target, original)
    if original and is_fallback(original["text"]) and (not is_fallback(text) or not enabled):
        raise ConfigError("兜底策略需保持启用并位于最后")
    if (not original or not is_fallback(original["text"])) and is_fallback(text):
        raise ConfigError("请编辑已有兜底策略，无需再添加一条")
    result = {"id": ident or "np_" + uuid.uuid4().hex, "text": text, "enabled": enabled}
    if name.strip():
        result["name"] = name.strip()
    return result


def prepare_request(native, request, fetch=False):
    """Add sources and route as one atomic edit; preview never performs I/O."""
    from .native_configuration import refresh_provider, rule_yaml_entries
    updated, draft = deepcopy(native), deepcopy(request.get("draft"))
    sources = request.get("sources", [])
    if not isinstance(draft, dict) or not isinstance(sources, list) or len(sources) > 16:
        raise ConfigError("规则表单无效，一次最多添加 16 个订阅")
    if sources and draft.get("mode") != "rules":
        raise ConfigError("仅已存规则方式可以添加订阅")
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ConfigError("订阅来源无效")
        name = source.get("name", "").strip() or draft.get("name", "").strip() + ("" if index == 0 else "-" + str(index + 1))
        if not name or len(name) > 80 or any(c in name for c in ",()\r\n"):
            raise ConfigError("请填写规则名称，避免逗号、括号和换行")
        providers = updated["mihomo"].setdefault("rule-providers", {})
        if name in providers:
            if source.get("name", "").strip():
                raise ConfigError("同名来源已存在，请更换来源名称")
            name = name[:60] + "-" + uuid.uuid4().hex[:8]
        url = source.get("url", "").strip()
        if urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname:
            raise ConfigError("请填写完整的 HTTP / HTTPS 订阅地址")
        behavior = source.get("behavior", "auto")
        if behavior not in {"auto", "domain", "ipcidr", "classical"}:
            raise ConfigError("规则类型无效")
        source_format = source.get("source_format", "payload")
        if source_format not in {"payload", "clash-rules"}:
            raise ConfigError("请选择 Mihomo 规则集或 Clash 完整规则列表")
        if source_format == "clash-rules":
            if behavior not in {"auto", "classical"}:
                raise ConfigError("完整规则列表需要 classical 类型")
            updated.setdefault("rule_source_adapters", {})[name] = {"format": "clash-rules"}
        providers[name] = {"type": "http", "behavior": "classical" if behavior == "auto" else behavior, "format": "yaml", "url": url, "path": "ruleset/user-" + uuid.uuid4().hex + ".yaml", "interval": 86400, "allow_private": bool(source.get("allow_private", False))}
        if fetch:
            updated = refresh_provider(updated, "rule-providers", name)
            providers = updated["mihomo"]["rule-providers"]
            if behavior == "auto":
                payload = rule_yaml_entries(updated["documents"][providers[name]["path"]])
                if not payload or any(not isinstance(line, str) for line in payload):
                    raise ConfigError("订阅需包含非空的 payload 或 rules 规则列表")
                def cidr(line):
                    try:ipaddress.ip_network(line, strict=False);return True
                    except ValueError:return False
                providers[name]["behavior"] = "classical" if any("," in line for line in payload) else "ipcidr" if all(cidr(line) for line in payload) else "domain"
        if updated.get("selection", {}).get("rules") is not None:
            updated["selection"]["rules"].append(name)
        draft.setdefault("rules", []).append(name)
    return updated, prepare(updated, draft, request.get("id"))


def change(native, request):
    if str(request.get("action", "")).startswith("unit-"):
        from .native_rule_groups import change as change_group
        return change_group(native, request)
    updated = deepcopy(native)
    action, ident = request.get("action"), request.get("id")
    row = next((p for p in updated["policies"] if p["id"] == ident), None)
    if action == "save":
        updated, prepared = prepare_request(native, request, fetch=True)
        row = next((p for p in updated["policies"] if p["id"] == ident), None)
        if row:
            updated["policies"][updated["policies"].index(row)] = prepared
        else:
            updated["policies"].insert(len(updated["policies"]) - 1, prepared)
    elif action == "reorder":
        ids = request.get("ids")
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(ids) != len(set(ids)) or set(ids) != {p["id"] for p in native["policies"]}:
            raise ConfigError("策略列表已变化，请刷新后重试")
        lookup = {p["id"]: p for p in updated["policies"]}
        if ids[-1] != native["policies"][-1]["id"]:
            raise ConfigError("兜底策略固定最后")
        updated["policies"] = [lookup[i] for i in ids]
    elif row:
        if action == "target":
            target = request.get("target")
            _validate_target(native, target, row)
            expression, _, params = split_target(row["text"])
            row["text"] = ",".join([expression, target, *params])
        elif action == "toggle" and not is_fallback(row["text"]) and type(request.get("enabled")) is bool:
            row["enabled"] = request["enabled"]
        elif action == "delete" and not is_fallback(row["text"]):
            updated["policies"].remove(row)
        elif action == "copy" and not is_fallback(row["text"]):
            copied = {**deepcopy(row), "id": "np_" + uuid.uuid4().hex, "name": row_view(native, row)["name"][:76] + " 副本"}
            updated["policies"].insert(updated["policies"].index(row) + 1, copied)
        else:
            raise ConfigError("不能停用、删除或复制兜底策略")
    else:
        raise ConfigError("策略不存在或操作无效，请刷新后重试")
    return updated
