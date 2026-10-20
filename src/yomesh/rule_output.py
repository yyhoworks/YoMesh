"""Package validated Profile rule sets using the chosen native storage mode."""
from pathlib import Path
import re

import yaml

from .model import ConfigError
from .rulelogic import references, singbox_expression


RULE_OUTPUTS = {"inline", "local", "remote"}


def remote_provider(profile, ident, proxy):
    # Inspect the original bytes: normalized entries alone cannot tell whether a
    # Clash payload contains bare domains or classical DOMAIN,... expressions.
    from .rule_sources import clean_source, fetch, parse_rules
    item = profile.index["library"][ident]
    source = clean_source(item["source"])
    body = fetch(source)
    parsed, detected = parse_rules(body, source["format"], Path(__file__).resolve().parents[2])
    kinds = {entry.split(",", 1)[0] for entry in parsed["entries"]}
    interval = source["interval_minutes"] * 60
    if proxy == "sing-box" and detected in {"sing-box", "srs"}:
        return {"type": "remote", "tag": "library_" + ident,
                "format": "binary" if detected == "srs" else "source",
                "url": source["url"], "update_interval": f"{interval}s",
                "http_client": {"headers": {"User-Agent": [source["user_agent"]]}}}
    if proxy == "mihomo" and detected in {"clash", "mrs"}:
        if detected == "mrs":
            behavior = "ipcidr" if kinds <= {"IP-CIDR", "IP-CIDR6"} else "domain"
        else:
            if not re.search(rb"(?m)^payload:\s*(?:#.*)?$", body):
                raise ConfigError(f"规则「{item['name']}」的 payload 不是 Mihomo 可直接读取的逐行 YAML；请选择本地文件或内联规则")
            payload = yaml.safe_load(body)["payload"]
            behavior = "classical" if all("," in entry for entry in payload) else "ipcidr" if kinds <= {"IP-CIDR", "IP-CIDR6"} else "domain"
            if any("," in entry for entry in payload) and not all("," in entry for entry in payload):
                raise ConfigError(f"规则「{item['name']}」混用裸值与 classical 行；请选择本地文件或内联规则")
            if behavior != "classical" and kinds & {"IP-CIDR", "IP-CIDR6"} and not kinds <= {"IP-CIDR", "IP-CIDR6"}:
                raise ConfigError(f"规则「{item['name']}」混用裸域名与 IP；请选择本地文件或内联规则")
        return {"type": "http", "behavior": behavior, "format": "mrs" if detected == "mrs" else "yaml",
                "url": source["url"], "path": f"./rules/cache_{ident}.{'mrs' if detected == 'mrs' else 'yaml'}",
                "interval": interval, "header": {"User-Agent": [source["user_agent"]]}}
    raise ConfigError(f"规则「{item['name']}」的原始 {detected} 格式不能直接供 {proxy} 订阅；请选择本地文件 / 内联规则，或换用目标内核支持的订阅格式")


def rule_assets(profile, proxy, mode):
    if mode not in RULE_OUTPUTS:
        raise ConfigError("规则输出方式需要 inline / local / remote")
    policies = profile.runtime_policies()
    all_used = set().union(*(references(p) for p in policies))
    used = all_used
    if mode == "inline":
        used = set().union(*(references(p) for p in policies if p["type"] == "LOGICAL")) if proxy == "mihomo" else set()
    definitions, files, assets = {}, {}, []
    for ident in sorted(used):
        item = profile.index["library"][ident]
        tag = "library_" + ident
        storage = "remote" if mode == "remote" and item.get("source", {}).get("type") == "url" else "inline" if mode == "inline" else "local"
        if storage == "remote":
            definition = remote_provider(profile, ident, proxy)
        elif proxy == "mihomo":
            payload = [kind + "," + value for kind, value in profile.criteria_cache[ident]]
            definition = {"type": "inline", "behavior": "classical", "payload": payload}
            if storage == "local":
                path = f"rules/{tag}.yaml"
                files[path] = yaml.safe_dump({"payload": payload}, allow_unicode=True, sort_keys=False)
                definition = {"type": "file", "behavior": "classical", "format": "yaml", "path": "./" + path}
        else:
            path = f"rules/{tag}.json"
            content = singbox_expression({"type": "RULE-SET", "value": ident}, profile)
            files[path] = {"version": 3, "rules": [content]}
            definition = {"type": "local", "tag": tag, "format": "source", "path": "./" + path}
        definitions[tag] = definition
    for ident in sorted(all_used):
        item = profile.index["library"][ident]
        storage = "remote" if mode == "remote" and item.get("source", {}).get("type") == "url" else "inline" if mode == "inline" else "local"
        assets.append({"id": ident, "name": item["name"], "storage": storage})
    return definitions, files, assets


def externalize_dns(config, files):
    for plugin in config["plugins"]:
        if plugin["type"] == "domain_set":
            path = "rules/mosdns_" + plugin["tag"] + ".txt"
            files[path] = "\n".join(plugin["args"]["exps"]) + "\n"
            plugin["args"] = {"files": ["./" + path]}
        elif plugin["type"] == "ip_set" and "ips" in plugin.get("args", {}):
            path = "rules/mosdns_" + plugin["tag"] + ".txt"
            files[path] = "\n".join(plugin["args"]["ips"]) + "\n"
            plugin["args"] = {"files": ["./" + path]}
