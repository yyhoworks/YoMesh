"""Target-specific base documents, parsed once by Profile and merged losslessly."""
import ipaddress
import json
from copy import deepcopy

import yaml

from .model import ConfigError

# Root fields from Mihomo v1.19.32 config.RawConfig and sing-box v1.14.3 option.Options.
MIHOMO_FIELDS = set("port socks-port redir-port tproxy-port mixed-port ss-config vmess-config inbound-tfo inbound-mptcp authentication skip-auth-prefixes lan-allowed-ips lan-disallowed-ips allow-lan bind-address mode unified-delay log-level ipv6 external-controller external-controller-routing-mark external-controller-pipe external-controller-unix external-controller-tls external-controller-cors external-ui external-ui-url external-ui-name external-doh-server secret interface-name routing-mark tunnels geo-auto-update geo-update-interval geodata-mode geodata-loader geosite-matcher tcp-concurrent find-process-mode global-client-fingerprint global-ua etag-support keep-alive-idle keep-alive-interval disable-keep-alive proxy-providers rule-providers proxies proxy-groups rules sub-rules listeners hosts dns ntp tun tuic-server iptables experimental profile geox-url sniffer tls clash-for-android".split())
SINGBOX_FIELDS = set("$schema log dns ntp certificate certificate_providers http_clients network_namespaces endpoints inbounds outbounds route services experimental".split())
MANAGED = {"mihomo": {"proxies", "proxy-groups", "proxy-providers", "rules", "rule-providers", "sub-rules", "listeners"}, "sing-box": {"outbounds"}}


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ConfigError("基础配置的字段名需要为不重复的文本")
        result[key] = value
    return result


def yaml_mapping(loader, node):
    return unique_pairs((loader.construct_object(k), loader.construct_object(v)) for k, v in node.value)


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def parse_base(text, target):
    if target not in MANAGED or not isinstance(text, str) or len(text.encode("utf-8")) > 256 * 1024:
        raise ConfigError("基础配置需要有效内核与不超过 256 KiB 的文本")
    if not text.strip():
        return {}
    try:
        result = yaml.load(text, Loader=UniqueLoader) if target == "mihomo" else json.loads(text, object_pairs_hook=unique_pairs)
        # Reject YAML aliases/cycles, non-JSON scalars and nonfinite numbers before persistence.
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (yaml.YAMLError, json.JSONDecodeError, ValueError, TypeError, RecursionError):
        raise ConfigError("基础配置语法无效；Mihomo 使用 YAML，sing-box 使用 JSON，不支持循环引用或非标准数值") from None
    if not isinstance(result, dict):
        raise ConfigError("基础配置顶层需要为对象；留空可恢复默认")
    unknown = set(result) - (MIHOMO_FIELDS if target == "mihomo" else SINGBOX_FIELDS)
    if unknown:
        raise ConfigError("基础配置含目标版本未支持的顶层字段：" + "、".join(sorted(unknown)))
    if set(result) & MANAGED[target]:
        raise ConfigError("节点、节点组、分流规则和绑定入站由工作区生成，请移除基础配置中的：" + "、".join(sorted(set(result) & MANAGED[target])))
    if target == "sing-box" and "route" in result:
        if not isinstance(result["route"], dict):
            raise ConfigError("sing-box route 需要为对象")
        if set(result["route"]) & {"rules", "rule_set", "final"}:
            raise ConfigError("route.rules / rule_set / final 由分流策略生成，请在工作区编辑")
    if target == "mihomo" and isinstance(result.get("external-ui"), str) and result["external-ui"].strip():
        from .native_configuration import normalize_ui_path
        result["external-ui"] = normalize_ui_path(result["external-ui"])
    return result


def parse_bases(values):
    if not isinstance(values, dict) or set(values) - set(MANAGED):
        raise ConfigError("base_configs 仅支持 mihomo / sing-box 文本")
    return {target: parse_base(text, target) for target, text in values.items()}


def merge_maps(original, custom):
    result = deepcopy(original)
    for key, value in custom.items():
        result[key] = merge_maps(result[key], value) if isinstance(result.get(key), dict) and isinstance(value, dict) else deepcopy(value)
    return result


def valid_port(value):
    return type(value) is int and 1024 <= value <= 65535


def address_port(value):
    if not value:
        return 0
    if not isinstance(value, str):
        raise ConfigError("控制接口 / DNS 监听地址需要为文本")
    try:
        return int(value.rsplit(":", 1)[1])
    except (ValueError, IndexError):
        return 0  # Preserve native syntax for the core's own validation.


def effective_settings(profile, target, require_socks=True):
    settings = deepcopy(profile.settings)
    base = profile.base_configs.get(target, {})
    address = "127.0.0.1"
    if target == "mihomo":
        for key in ("mixed-port", "socks-port", "port", "redir-port", "tproxy-port"):
            if key in base and base[key] != 0 and not valid_port(base[key]):
                raise ConfigError(f"基础配置 {key} 需要为 0 或 1024–65535 整数")
        port = base.get("socks-port") or base.get("mixed-port", settings["mixed_port"])
        if require_socks and not valid_port(port):
            raise ConfigError("配套 MosDNS 需要保留有效的 mixed-port 或 socks-port")
        if base.get("allow-lan"):
            address = base.get("bind-address", "*")
            if address in {"*", "0.0.0.0", "::"}:
                address = "127.0.0.1"
    else:
        port = settings["mixed_port"]
        if "inbounds" in base:
            items = base["inbounds"]
            if not isinstance(items, list) or not all(isinstance(i, dict) and isinstance(i.get("tag"), str) for i in items):
                raise ConfigError("基础配置 inbounds 需要包含 tag 的入站对象数组")
            tags = [i["tag"] for i in items]
            if len(tags) != len(set(tags)):
                raise ConfigError("基础配置入站 tag 不能重复")
            mixed = next((i for i in items if i["tag"] == "mixed-in"), None)
            if require_socks and (not mixed or mixed.get("type") not in {"mixed", "socks"} or not valid_port(mixed.get("listen_port"))):
                raise ConfigError("基础配置需保留有效 mixed-in（mixed / socks）入站，供 MosDNS 使用")
            port = mixed.get("listen_port", 0) if mixed and mixed.get("type") in {"mixed", "socks"} else 0
            address = mixed.get("listen", "127.0.0.1") if port else "127.0.0.1"
            if address in {"0.0.0.0", "::"}:
                address = "127.0.0.1"
    try:
        ipaddress.ip_address(address)
    except (ValueError, TypeError):
        raise ConfigError("基础配置统一 SOCKS 监听需要为 IP 或通配地址") from None
    if (require_socks and port == settings["dns_port"]) or any(i.get("enabled", True) and i["port"] == port for i in profile.inbounds):
        raise ConfigError("基础配置统一 SOCKS 端口与 MosDNS / 绑定入站冲突")
    ports = [i["port"] for i in profile.inbounds if i.get("enabled", True)] + ([settings["dns_port"]] if require_socks else [])
    if target == "mihomo":
        settings["mixed_port"] = base.get("mixed-port", settings["mixed_port"])
        ports.extend([settings["mixed_port"], *(base.get(key, 0) for key in ("socks-port", "port", "redir-port", "tproxy-port"))])
        ports.extend(address_port(base.get(key)) for key in ("external-controller", "external-controller-tls"))
        if isinstance(base.get("dns"), dict) and base["dns"].get("enable", True):
            ports.append(address_port(base["dns"].get("listen")))
    else:
        settings["mixed_port"] = port
        ports.extend([i.get("listen_port", 0) for i in base.get("inbounds", [{"listen_port": port}])])
        experimental = base.get("experimental")
        if isinstance(experimental, dict) and isinstance(experimental.get("clash_api"), dict):
            ports.append(address_port(experimental["clash_api"].get("external_controller")))
    ports = [p for p in ports if type(p) is int and p > 0]
    if len(ports) != len(set(ports)):
        raise ConfigError("基础配置监听端口与其他基础入站、绑定入站或 MosDNS 冲突")
    return settings, (f"[{address}]:{port}" if ":" in address else f"{address}:{port}") if port else None


def apply_base(profile, target, generated):
    base = deepcopy(profile.base_configs.get(target, {}))
    if target == "sing-box" and "inbounds" in base:
        bound = [i for i in generated["inbounds"] if i["tag"] != "mixed-in"]
        if {i["tag"] for i in bound} & {i["tag"] for i in base["inbounds"]}:
            raise ConfigError("基础入站 tag 与工作区绑定入站冲突")
        base["inbounds"].extend(bound)
    result = merge_maps(generated, base)
    # A template must preserve the domain sniffing required by bound plans.
    if target == "mihomo" and any(i.get("enabled", True) and i.get("sniff") for i in profile.inbounds) and not (result.get("sniffer") or {}).get("enable"):
        raise ConfigError("绑定入站启用了域名嗅探，基础配置不能关闭 sniffer.enable")
    return result


NATIVE_MANAGED = {"rules", "proxy-groups", "proxy-providers", "rule-providers", "proxies", "listeners"}


def prepare_base_profile(value, target, text):
    """Apply an editor draft to its authoritative document, preserving resources."""
    candidate = deepcopy(value)
    if "native_configuration" in candidate and target == "mihomo":
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 512 * 1024:
            raise ConfigError("Mihomo 原生基础配置不能为空，且最多 512 KiB")
        try:
            base = yaml.load(text, Loader=UniqueLoader)
        except yaml.YAMLError:
            raise ConfigError("Mihomo 基础配置不是有效 YAML") from None
        if not isinstance(base, dict) or set(base) & NATIVE_MANAGED:
            raise ConfigError("基础配置需要对象；节点、节点组、规则与入站请在各自入口编辑")
        if isinstance(base.get("external-ui"), str) and base["external-ui"].strip():
            from .native_configuration import normalize_ui_path
            base["external-ui"] = normalize_ui_path(base["external-ui"])
        original = candidate["native_configuration"]["mihomo"]
        candidate["native_configuration"]["mihomo"] = {**base, **{key: deepcopy(original[key]) for key in NATIVE_MANAGED if key in original}}
    else:
        parse_base(text, target)
        candidate.setdefault("base_configs", {})[target] = text
    return candidate


def base_editor(profile, native=None):
    s = profile.settings
    templates = {
        "mihomo": {"mixed-port": s["mixed_port"], "allow-lan": False, "bind-address": "127.0.0.1", "ipv6": True, "mode": "rule", "log-level": "info", "unified-delay": True, "tcp-concurrent": True, "profile": {"store-selected": True}, "dns": {"enable": True, "enhanced-mode": "redir-host", "nameserver": [f"udp://127.0.0.1:{s['dns_port']}"], "default-nameserver": [s["bootstrap"]], "proxy-server-nameserver": [s["bootstrap"]]}, "tun": {"enable": False}},
        "sing-box": {"log": {"level": "info"}, "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": s["mixed_port"]}], "dns": {"servers": [{"type": "udp", "tag": "bootstrap", "server": s["bootstrap"]}, {"type": "udp", "tag": "mosdns", "server": "127.0.0.1", "server_port": s["dns_port"]}], "final": "mosdns"}, "route": {"default_domain_resolver": {"server": "bootstrap"}}},
    }
    stored = deepcopy(profile.data.get("base_configs", {}))
    if native is not None:
        stored["mihomo"] = yaml.safe_dump({key: value for key, value in native["mihomo"].items() if key not in NATIVE_MANAGED}, allow_unicode=True, sort_keys=False)
    return {"native": native is not None, "stored": stored, "defaults": {"mihomo": "# Mihomo 1.19.32 基础配置；节点、节点组与分流规则由工作区合并。\n" + yaml.safe_dump(templates["mihomo"], allow_unicode=True, sort_keys=False), "sing-box": json.dumps(templates["sing-box"], ensure_ascii=False, indent=2) + "\n"}}
