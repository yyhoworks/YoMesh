"""Validated rule expressions shared by the editor and every native compiler."""
from .model import ConfigError, MATCHES, match_value


def condition_expression(condition, library, path):
    if not isinstance(condition, dict) or set(condition) - {"type", "value", "values"}:
        raise ConfigError(path + ": 条件字段无效")
    kind = condition.get("type")
    if "value" in condition and "values" in condition:
        raise ConfigError(path + ": 匹配值不能同时使用 value 与 values")
    if kind not in MATCHES:
        raise ConfigError(path + ": 未支持的匹配类型")
    if kind == "RULE-SET":
        if set(condition) != {"type", "value"} or condition.get("value") not in library:
            raise ConfigError(path + ": 规则集引用不存在")
        return {"type": kind, "value": condition["value"]}
    values = condition.get("values", [condition.get("value")])
    if not isinstance(values, list) or not 1 <= len(values) <= 10000:
        raise ConfigError(path + ": 每个匹配器需要 1–10000 个值")
    for value in values:
        match_value(kind, value, path)
    return {"type": kind, "values": list(dict.fromkeys(values))}


def expression(policy, library):
    if policy.get("type") != "LOGICAL":
        return condition_expression({"type": policy.get("type"), "value": policy.get("value")}, library, "规则条件")
    groups = policy.get("condition_groups")
    operator = policy.get("group_operator", "or")
    if operator not in {"and", "or"} or not isinstance(groups, list) or not 1 <= len(groups) <= 16:
        raise ConfigError("规则需要 1–16 个条件组，组间关系需要 AND / OR")
    result, count = [], 0
    for index, group in enumerate(groups):
        if not isinstance(group, dict) or set(group) != {"operator", "conditions"} or group.get("operator") not in {"and", "or"}:
            raise ConfigError("条件组需要 AND / OR 关系与条件列表")
        conditions = group["conditions"]
        if not isinstance(conditions, list) or not conditions:
            raise ConfigError("每个条件组至少需要一个条件")
        count += len(conditions)
        if count > 64:
            raise ConfigError("每条规则最多 64 个条件")
        result.append({"op": group["operator"], "items": [condition_expression(c, library, f"条件组 {index + 1}") for c in conditions]})
    return {"op": operator, "items": result}


def references(policy):
    if policy.get("type") == "RULE-SET":
        return {policy["value"]}
    return {c["value"] for group in policy.get("condition_groups", []) for c in group.get("conditions", []) if c.get("type") == "RULE-SET"}


def leaf_criteria(expr, profile):
    return profile.criteria_cache[expr["value"]] if expr["type"] == "RULE-SET" else [(expr["type"], v) for v in expr["values"]]


def describe(expr, library):
    if "op" in expr:
        texts = [describe(item, library) for item in expr["items"]]
        return texts[0] if len(texts) == 1 else "(" + (" 且 " if expr["op"] == "and" else " 或 ").join(texts) + ")"
    if expr["type"] == "RULE-SET":
        item = library[expr["value"]]
        return item["name"] + f"（{len(item['entries'])} 条）"
    return expr["type"] + " " + " / ".join(expr["values"][:2]) + (f" 等 {len(expr['values'])} 项" if len(expr["values"]) > 2 else "")


def mihomo_expression(expr, profile):
    if "op" in expr:
        parts = [mihomo_expression(item, profile) for item in expr["items"]]
        return parts[0] if len(parts) == 1 else expr["op"].upper() + ",(" + ",".join("(" + p + ")" for p in parts) + ")"
    if expr["type"] == "RULE-SET":
        no_resolve = any(kind.startswith("IP-CIDR") for kind, _ in profile.criteria_cache[expr["value"]])
        return "RULE-SET,library_" + expr["value"] + (",no-resolve" if no_resolve else "")
    parts = [",".join([kind, value] + (["no-resolve"] if kind.startswith("IP-CIDR") else [])) for kind, value in leaf_criteria(expr, profile)]
    return parts[0] if len(parts) == 1 else "OR,(" + ",".join("(" + p + ")" for p in parts) + ")"


def singbox_expression(expr, profile, ruleset_refs=False):
    if "op" in expr:
        parts = [singbox_expression(item, profile, ruleset_refs) for item in expr["items"]]
        return parts[0] if len(parts) == 1 else {"type": "logical", "mode": expr["op"], "rules": parts}
    if ruleset_refs and expr["type"] == "RULE-SET":
        return {"rule_set": ["library_" + expr["value"]]}
    fields = {}
    names = {"DOMAIN": "domain", "DOMAIN-SUFFIX": "domain_suffix", "DOMAIN-KEYWORD": "domain_keyword", "DOMAIN-REGEX": "domain_regex", "IP-CIDR": "ip_cidr", "IP-CIDR6": "ip_cidr"}
    for kind, value in leaf_criteria(expr, profile):
        fields.setdefault(names[kind], []).append(value)
    parts = [{key: values} for key, values in fields.items()]
    return parts[0] if len(parts) == 1 else {"type": "logical", "mode": "or", "rules": parts}


def dns_expression(expr, profile):
    """IP predicates cannot be known from a DNS question. Never widen an AND."""
    if "op" in expr:
        parts = [dns_expression(item, profile) for item in expr["items"]]
        if expr["op"] == "and" and any(part is None for part in parts):
            return None
        parts = [part for part in parts if part is not None]
        return {"op": expr["op"], "items": parts} if parts else None
    criteria = leaf_criteria(expr, profile)
    domains = [(kind, value) for kind, value in criteria if not kind.startswith("IP-CIDR")]
    return {"criteria": domains} if domains else None


def has_ip(expr, profile):
    if "op" in expr:
        return any(has_ip(item, profile) for item in expr["items"])
    return any(kind.startswith("IP-CIDR") for kind, _ in leaf_criteria(expr, profile))


def mosdns_expression(expr, profile, plugins, branch, prefix, miss):
    """Compile boolean evaluation as short-circuit sequences; no DNF explosion."""
    dns = dns_expression(expr, profile)
    if dns is None:
        return None
    counter = [0]

    def emit(node, yes, no):
        counter[0] += 1
        tag = prefix + "_" + str(counter[0])
        if "criteria" in node:
            names = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:", "DOMAIN-REGEX": "regexp:"}
            matcher = tag + "_match"
            plugins.append({"tag": matcher, "type": "domain_set", "args": {"exps": [names[k] + v for k, v in node["criteria"]]}})
            plugins.append({"tag": tag, "type": "sequence", "args": [{"matches": "qname $" + matcher, "exec": "goto " + yes}, {"exec": "goto " + no}]})
            return tag
        continuation = yes if node["op"] == "and" else no
        for item in reversed(node["items"]):
            continuation = emit(item, continuation, no) if node["op"] == "and" else emit(item, yes, continuation)
        return continuation

    return emit(dns, branch, miss)
