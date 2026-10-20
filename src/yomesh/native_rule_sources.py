"""Explicit adapters for source formats that Mihomo cannot load remotely."""
from copy import deepcopy
import uuid

import yaml

from .model import ConfigError


def validate_adapters(native):
    adapters = native.get("rule_source_adapters", {})
    providers = native["mihomo"].get("rule-providers", {})
    if not isinstance(adapters, dict):
        raise ConfigError("规则来源适配设置需要对象")
    for name, adapter in adapters.items():
        if name not in providers or not isinstance(adapter, dict) or adapter.get("format") != "clash-rules" or set(adapter) - {"format", "source_path", "source_target"}:
            raise ConfigError("规则来源适配设置无效")
        if "source_path" in adapter:
            from .native_configuration import safe_path
            safe_path(adapter["source_path"])
            if adapter["source_path"] not in native.get("documents", {}):
                raise ConfigError("缺少规则来源原文件")
        if "source_target" in adapter and not isinstance(adapter["source_target"], str):
            raise ConfigError("规则来源出口记录无效")


def normalize_rule_list(text):
    """Keep each predicate and suffix; record the one source routing label."""
    from .native_policy import decode, split_target
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        raise ConfigError("完整规则列表需要有效 YAML") from None
    if not isinstance(value, dict) or set(value) != {"rules"} or not isinstance(value["rules"], list) or not value["rules"] or len(value["rules"]) > 250000:
        raise ConfigError("完整规则列表只支持非空 rules 数组，其他字段请在基础配置中设置")
    payload, targets = [], set()
    for line in value["rules"]:
        if not isinstance(line, str) or "\n" in line or "\r" in line:
            raise ConfigError("完整规则列表需要单行规则")
        spec = decode(line)
        if spec["mode"] != "matcher" or len(spec["values"]) != 1:
            raise ConfigError("完整规则列表仅支持单项匹配器；复杂关系请在高级条件配置")
        from .native_policy import _validate_value
        _validate_value(spec["matcher_type"], spec["values"][0])
        expression, target, params = split_target(line)
        payload.append(",".join([expression, *params]))
        targets.add(target)
    if len(targets) != 1:
        raise ConfigError("来源包含多个出口，不能合为一个规则组；请分别配置以保留分流行为")
    return yaml.safe_dump({"payload": payload}, allow_unicode=True, sort_keys=False), next(iter(targets))


def cache_rule_list(native, name, text):
    adapter = native["rule_source_adapters"][name]
    normalized, target = normalize_rule_list(text)
    path = adapter.setdefault("source_path", "archive/rule-sources/" + uuid.uuid4().hex + ".yaml")
    native.setdefault("documents", {})[path] = text
    adapter["source_target"] = target
    return normalized


def copy_adapter(native, source, destination):
    adapter = native.get("rule_source_adapters", {}).get(source)
    if not adapter:
        return
    copied = deepcopy(adapter)
    if copied.get("source_path"):
        path = "archive/rule-sources/" + uuid.uuid4().hex + ".yaml"
        native["documents"][path] = native["documents"][copied["source_path"]]
        copied["source_path"] = path
    native.setdefault("rule_source_adapters", {})[destination] = copied


def export_adapters(native, config):
    adapters = []
    for name, adapter in native.get("rule_source_adapters", {}).items():
        provider = config.get("rule-providers", {}).get(name)
        if provider is None:
            continue
        if not adapter.get("source_path") or provider.get("behavior") != "classical":
            raise ConfigError("完整规则列表需要先成功读取并使用 classical 类型")
        # Preserve the source URL and all provider fields; file type loads the
        # normalized package snapshot rather than an incompatible remote file.
        provider["type"] = "file"
        adapters.append({"name": name, "source_format": adapter["format"], "source_url": provider.get("url", ""), "source_path": adapter["source_path"], "source_target": adapter.get("source_target"), "storage": "local-snapshot"})
    return adapters
