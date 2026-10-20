"""Read-only rule inspection. No cache downloads or changes to routing."""
from collections import defaultdict
import base64
import binascii
import re
import yaml

from .model import ConfigError
from .native_configuration import rule_yaml_entries
from .native_policy import split_target, _children


def rule_entries(native, provider):
    if provider.get("type") == "inline":
        entries = provider.get("payload", [])
        if not isinstance(entries, list) or any(not isinstance(v, str) for v in entries):
            raise ConfigError("规则内容需要字符串列表")
        return entries
    format_ = provider.get("format", "yaml")
    if format_ not in {"yaml", "text"}:
        raise ConfigError("MRS / 二进制缓存未展开")
    path = str(provider.get("path", "")).removeprefix("./")
    body = native.get("documents", {}).get(path)
    if body is None and path in native.get("assets", {}):
        try:
            body = base64.b64decode(native["assets"][path], validate=True).decode("utf-8-sig")
        except (ValueError, UnicodeError, binascii.Error):
            raise ConfigError("缓存不是可读取的 UTF-8 规则内容") from None
    if body is None:
        raise ConfigError("尚无可读取的缓存")
    if format_ == "yaml":
        return rule_yaml_entries(body)
    if format_ == "text":
        return [line.strip() for line in body.splitlines() if line.strip() and not line.lstrip().startswith(("#", "//"))]
    raise ConfigError("MRS / 二进制缓存未展开")


def entry_expression(entry, behavior):
    """Keep whole logical expressions; never report their leaves as duplicates."""
    if behavior == "domain":
        if entry.startswith("+."):
            return "DOMAIN-SUFFIX," + entry[2:]
        if "*" in entry or "?" in entry:
            return "DOMAIN-PATTERN," + entry
        return "DOMAIN," + entry
    if behavior == "ipcidr":
        return ("IP-CIDR6," if ":" in entry else "IP-CIDR,") + entry
    # Classical providers accept a redundant action. Do not strip unknown syntax.
    if entry.startswith(("AND,", "OR,", "NOT,")):
        if _children(entry) or entry.startswith("NOT,(") and entry.endswith(")"):
            return entry
        try:
            condition, _, params = split_target(entry)
            if _children(condition) or condition.startswith("NOT,(") and condition.endswith(")"):
                return ",".join([condition, *params])
        except ConfigError:
            pass
    atom = re.fullmatch(r"([A-Z][A-Z0-9-]*),([^,()]+)(?:,([^,()]+))?(?:,(no-resolve|src|dst))?", entry)
    known = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX", "IP-CIDR", "IP-CIDR6", "SRC-IP-CIDR", "NETWORK", "DST-PORT", "SRC-PORT", "IN-NAME", "RULE-SET", "GEOIP", "GEOSITE", "PROCESS-NAME", "PROCESS-PATH", "IP-ASN"}
    if atom and atom[1] in known:
        suffix = [x for x in (atom[3], atom[4]) if x in {"no-resolve", "src", "dst"}]
        return ",".join([atom[1], atom[2], *suffix])
    return entry


def scan_rule_entries(native, limit=250000, display_limit=100):
    """Find exact conditions across direct rules and cached provider entries.

    A shared provider is scanned once. Its routes are context, not separate
    duplicate entries. Bounded output and explicit coverage avoid false 'clean'.
    """
    providers = native["mihomo"].get("rule-providers", {})
    usage = defaultdict(list)
    for position, row in enumerate(native["policies"], 1):
        expression, target, _ = split_target(row["text"])
        for ref in dict.fromkeys(re.findall(r"RULE-SET,([^,)]+)", expression)):
            usage[ref].append({"id": row["id"], "position": position, "target": target, "enabled": row["enabled"]})
    first, repeats, counts, skipped = {}, {}, {}, []
    scanned = 0
    capped = False

    def add(expression, item):
        nonlocal scanned, capped
        if scanned >= limit:
            capped = True
            return False
        scanned += 1
        if expression in first:
            counts[expression] = counts.get(expression, 1) + 1
            if expression not in repeats and len(repeats) < display_limit:
                repeats[expression] = [first[expression]]
            if expression in repeats and len(repeats[expression]) < 12:
                repeats[expression].append(item)
        else:
            first[expression] = item
        return True

    for position, row in enumerate(native["policies"], 1):
        expression, target, params = split_target(row["text"])
        if expression not in {"MATCH", "FINAL"}:
            add(",".join([expression, *params]), {"kind": "policy", "id": row["id"], "name": row.get("name") or expression[:70], "position": position, "target": target, "enabled": row["enabled"]})
    provider_count = 0
    for name, provider in providers.items():
        if capped:
            skipped.append({"name": name, "reason": "达到本次扫描上限"})
            continue
        try:
            entries = rule_entries(native, provider)
        except (ConfigError, ValueError, TypeError, yaml.YAMLError):
            skipped.append({"name": name, "reason": "缓存缺失、格式无效或 MRS / 二进制未展开"})
            continue
        provider_count += 1
        for index, entry in enumerate(entries, 1):
            if not add(entry_expression(entry, provider.get("behavior", "classical")), {"kind": "provider", "provider": name, "position": index, "routes": usage[name][:12], "route_count": len(usage[name])}):
                skipped.append({"name": name, "reason": "只扫描部分条目：达到本次扫描上限"})
                break
    return {"groups": [{"expression": expression, "count": counts[expression], "occurrences": rows} for expression, rows in repeats.items()],
            "duplicate_count": len(counts), "scanned_entries": scanned, "scanned_providers": provider_count, "total_providers": len(providers),
            "skipped": skipped, "complete": not skipped and not capped, "display_limit": display_limit,
            "note": "比较完整条件，不拆开 AND / OR；相同内容不等于可以删除。规则集引用位置仅作上下文，实际出口受逻辑条件、顺序与启停影响。"}
