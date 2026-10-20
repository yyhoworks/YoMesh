"""Compile one profile into native configurations with explicit target limits."""
from __future__ import annotations

from copy import deepcopy

from .model import ConfigError, Profile
from .rulelogic import expression, references, mihomo_expression, singbox_expression, has_ip, mosdns_expression
from .inbounds import mihomo_listener, singbox_listener, tag as inbound_tag


def node_mihomo(node, tag):
    kind = node["protocol"]
    out = {"name": tag, "type": "ss" if kind == "shadowsocks" else kind, "server": node["server"], "port": node["port"], "udp": node.get("udp", True)}
    for field in ("password", "uuid", "flow"):
        if field in node:
            out[field] = node[field]
    if kind == "shadowsocks":
        out["cipher"] = node["method"]
    if kind == "vmess":
        out.update({"alterId": node.get("alter_id", 0), "cipher": node.get("security", "auto")})
    tls = node["tls"]
    if tls.get("enabled"):
        if kind in {"vless", "vmess"}:
            out["tls"] = True
        if tls.get("server_name"):
            out["servername" if kind in {"vless", "vmess"} else "sni"] = tls["server_name"]
        out["skip-cert-verify"] = tls.get("insecure", False)
        if tls.get("fingerprint"):
            out["client-fingerprint"] = tls["fingerprint"]
        if tls.get("alpn"):
            out["alpn"] = tls["alpn"]
        if tls.get("reality"):
            out["client-fingerprint"] = tls.get("fingerprint", "chrome")
            out["reality-opts"] = {"public-key": tls["reality"]["public_key"], "short-id": tls["reality"].get("short_id", "")}
    transport = node.get("transport")
    if transport:
        out["network"] = transport["type"]
        if transport["type"] == "ws":
            out["ws-opts"] = {"path": transport.get("path", "/"), "headers": transport.get("headers", {})}
        else:
            out["grpc-opts"] = {"grpc-service-name": transport.get("service_name", "")}
    if node.get("obfs"):
        out["obfs"] = node["obfs"]["type"]
        out["obfs-password"] = node["obfs"]["password"]
    return out


def node_singbox(node, tag):
    kind = node["protocol"]
    out = {"tag": tag, "type": kind, "server": node["server"], "server_port": node["port"]}
    for field in ("password", "uuid", "flow"):
        if field in node:
            out[field] = node[field]
    if kind == "shadowsocks":
        out["method"] = node["method"]
    if kind == "vmess":
        out.update({"alter_id": node.get("alter_id", 0), "security": node.get("security", "auto")})
    tls = node["tls"]
    if tls.get("enabled"):
        out["tls"] = {"enabled": True, "insecure": tls.get("insecure", False)}
        if tls.get("server_name"):
            out["tls"]["server_name"] = tls["server_name"]
        if tls.get("alpn"):
            out["tls"]["alpn"] = tls["alpn"]
        if tls.get("fingerprint") or tls.get("reality"):
            out["tls"]["utls"] = {"enabled": True, "fingerprint": tls.get("fingerprint", "chrome")}
        if tls.get("reality"):
            out["tls"]["reality"] = {"enabled": True, **tls["reality"]}
    if node.get("transport"):
        out["transport"] = node["transport"].copy()
    if node.get("udp") is False:
        out["network"] = "tcp"
    if node.get("obfs"):
        out["obfs"] = node["obfs"].copy()
    return out


def mihomo_rules(profile, policies, fallback, ruleset_refs=False):
    rules = []
    for policy in policies:
        if not policy.get("enabled", True):
            continue
        if policy["type"] == "LOGICAL" or (ruleset_refs and policy["type"] == "RULE-SET"):
            expr = expression(policy, profile.index["library"])
            predicate = mihomo_expression(expr, profile)
            # A collapsed single predicate places options after the outbound.
            # Nested logical predicates keep their options inside parentheses.
            suffix = ",no-resolve" if predicate.endswith(",no-resolve") else ""
            rules.append(predicate.removesuffix(suffix) + "," + profile.tag(policy["target"]) + suffix if suffix else predicate + "," + profile.tag(policy["target"]))
            continue
        for kind, value in profile.criteria(policy):
            rules.append(",".join([kind, value, profile.tag(policy["target"])] + (["no-resolve"] if kind.startswith("IP-CIDR") else [])))
    rules.append("MATCH," + profile.tag(fallback))
    return rules


def compile_mihomo(profile: Profile, ruleset_refs=False):
    settings = profile.settings
    groups = []
    for group in profile.data["groups"]:
        if not group.get("enabled", True):
            continue
        result = {"name": profile.tag(group["id"]), "type": group["mode"], "proxies": [profile.tag(m) for m in profile.members(group["id"])]}
        if group["mode"] != "select":
            result.update({"url": group.get("test_url", "https://www.gstatic.com/generate_204"), "interval": group.get("interval", 300)})
        if group["mode"] == "url-test":
            result["tolerance"] = group.get("tolerance", 50)
        if group["mode"] == "load-balance":
            result["strategy"] = "consistent-hashing"
        groups.append(result)
    result = {"mixed-port": settings["mixed_port"], "allow-lan": False, "bind-address": "127.0.0.1", "mode": "rule", "log-level": "info", "dns": {"enable": True, "enhanced-mode": "redir-host", "nameserver": [f"udp://127.0.0.1:{settings['dns_port']}"], "default-nameserver": [settings["bootstrap"]], "proxy-server-nameserver": [settings["bootstrap"]]}, "proxies": [node_mihomo(n, profile.tag(n["id"])) for n in profile.data["nodes"] if profile.active(n)], "proxy-groups": groups, "rules": mihomo_rules(profile, profile.data["policies"], profile.data["fallback"], ruleset_refs)}
    enabled = [i for i in profile.inbounds if i.get("enabled", True)]
    if enabled:
        active = profile.data.get("active_plan")
        result["listeners"] = [mihomo_listener(i, active) for i in enabled]
        for ident in dict.fromkeys(i["plan"] for i in enabled if i["plan"] != active):
            plan = profile.routing_plans[ident]
            result.setdefault("sub-rules", {})["plan_" + ident] = mihomo_rules(profile, plan["policies"], plan["fallback"], ruleset_refs)
        if any(i.get("sniff") for i in enabled):
            result["sniffer"] = {"enable": True, "parse-pure-ip": True, "force-dns-mapping": True, "override-destination": False, "sniff": {kind: {"ports": ["1-65535"]} for kind in ("HTTP", "TLS", "QUIC")}}
    policies = profile.runtime_policies()
    used = set().union(*(references(p) for p in policies if p.get("enabled", True) and p["type"] == "LOGICAL"))
    if used:
        result["rule-providers"] = {"library_" + ident: {"type": "inline", "behavior": "classical", "payload": [kind + "," + value for kind, value in profile.criteria_cache[ident]]} for ident in sorted(used)}
    return result


def compile_singbox(profile: Profile, ruleset_refs=False):
    settings = profile.settings
    outbounds = [{"type": "direct", "tag": "DIRECT"}]
    outbounds.extend(node_singbox(n, profile.tag(n["id"])) for n in profile.data["nodes"] if profile.active(n))
    for group in profile.data["groups"]:
        if not group.get("enabled", True):
            continue
        if group["mode"] not in {"select", "url-test"}:
            raise ConfigError(f"groups.{group['id']}.mode: sing-box 1.14.3 无原生 {group['mode']} 等价实现；请改为手动选择或自动测速，或选择 Mihomo 导出")
        item = {"type": "selector" if group["mode"] == "select" else "urltest", "tag": profile.tag(group["id"]), "outbounds": [profile.tag(m) for m in profile.members(group["id"])]}
        if group["mode"] == "url-test":
            item.update({"url": group.get("test_url", "https://www.gstatic.com/generate_204"), "interval": f"{group.get('interval', 300)}s", "tolerance": group.get("tolerance", 50)})
        elif group.get("selected"):
            item["default"] = profile.tag(group["selected"])
        outbounds.append(item)
    routes = []
    enabled = [i for i in profile.inbounds if i.get("enabled", True)]
    sniffing = [inbound_tag(i) for i in enabled if i.get("sniff")]
    if sniffing:
        routes.append({"inbound": sniffing, "action": "sniff"})
    for inbound in enabled:
        plan = profile.routing_plans[inbound["plan"]]
        scope = {"inbound": [inbound_tag(inbound)]}
        routes.extend(singbox_rules(profile, plan["policies"], scope, ruleset_refs))
        routes.append({**scope, "action": "route", "outbound": profile.tag(plan["fallback"])})
    routes.extend(singbox_rules(profile, profile.data["policies"], ruleset_refs=ruleset_refs))
    return {"log": {"level": "info"}, "dns": {"servers": [{"type": "udp", "tag": "bootstrap", "server": settings["bootstrap"]}, {"type": "udp", "tag": "mosdns", "server": "127.0.0.1", "server_port": settings["dns_port"]}], "final": "mosdns"}, "inbounds": [{"type": "mixed", "tag": "mixed-in", "listen": "127.0.0.1", "listen_port": settings["mixed_port"]}, *[singbox_listener(i) for i in enabled]], "outbounds": outbounds, "route": {"rules": routes, "final": profile.tag(profile.data["fallback"]), "default_domain_resolver": {"server": "bootstrap"}}}


def singbox_rules(profile, policies, scope=None, ruleset_refs=False):
    routes = []
    field_map = {"DOMAIN": "domain", "DOMAIN-SUFFIX": "domain_suffix", "DOMAIN-KEYWORD": "domain_keyword", "DOMAIN-REGEX": "domain_regex", "IP-CIDR": "ip_cidr", "IP-CIDR6": "ip_cidr"}
    for policy in policies:
        if not policy.get("enabled", True):
            continue
        if policy["type"] == "LOGICAL" or (ruleset_refs and policy["type"] == "RULE-SET"):
            rule = singbox_expression(expression(policy, profile.index["library"]), profile, ruleset_refs)
            if scope and rule.get("type") == "logical":
                rule = {"type": "logical", "mode": "and", "rules": [dict(scope), rule]}
            elif scope:
                rule = {**rule, **scope}
            rule.update({"action": "reject", "method": "default"} if policy["target"] == "REJECT" else {"action": "route", "outbound": profile.tag(policy["target"])})
            routes.append(rule)
            continue
        # Values in one field are OR matches. Separate fields into adjacent rules
        # with the same action; combining fields would accidentally create AND.
        fields = {}
        for kind, value in profile.criteria(policy):
            fields.setdefault(field_map[kind], []).append(value)
        for field, values in fields.items():
            rule = {**(scope or {}), field: values}
            if policy["target"] == "REJECT":
                rule.update({"action": "reject", "method": "default"})
            else:
                rule.update({"action": "route", "outbound": profile.tag(policy["target"])})
            routes.append(rule)
    return routes


def compile_mosdns(profile: Profile):
    if "mosdns" in profile.data:
        from .mosdns import compile_config
        return compile_config(profile.data["mosdns"]), []
    s = profile.settings
    plugins = [
        {"tag": "local_dns", "type": "forward", "args": {"upstreams": [{"addr": s["local_upstream"]}]}},
        {"tag": "remote_dns", "type": "forward", "args": {"socks5": s.get("dns_socks5") or getattr(profile, "native_socks_address", f"127.0.0.1:{s['mixed_port']}"), "bootstrap": s["bootstrap"], "upstreams": [{"addr": s["remote_upstream"]}]}},
        {"tag": "cache", "type": "cache", "args": {"size": 1024}},
    ]
    entry = "default_dns"
    plugins.append({"tag": entry, "type": "sequence", "args": [{"exec": "$" + s["dns_default"] + "_dns"}, {"exec": "accept"}]})
    diagnostics = []
    for policy in reversed(profile.enabled_policies() if s.get("dns_follow_rules", True) else []):
        dns = policy.get("dns", "auto")
        action = "reject 3" if policy["target"] == "REJECT" else "$" + dns + "_dns" if dns != "auto" else "$local_dns" if policy["target"] == "DIRECT" else "$remote_dns"
        if policy["type"] == "LOGICAL":
            expr = expression(policy, profile.index["library"])
            branch = "branch_" + policy["id"]
            plugins.append({"tag": branch, "type": "sequence", "args": [{"exec": action}, {"exec": "accept"}]})
            condition_entry = mosdns_expression(expr, profile, plugins, branch, "condition_" + policy["id"], entry)
            if condition_entry:
                entry = condition_entry
            if has_ip(expr, profile):
                diagnostics.append({"policy": policy["id"], "scope": "proxy", "message": "IP 条件在代理侧执行；含 IP 的 AND 条件不降级为纯域名 DNS 匹配"})
            continue
        criteria = profile.criteria(policy)
        prefixes = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:", "DOMAIN-REGEX": "regexp:"}
        exps = [prefixes[kind] + value for kind, value in criteria if kind in prefixes]
        if len(exps) != len(criteria):
            diagnostics.append({"policy": policy["id"], "scope": "proxy", "message": "IP-CIDR 保留在代理路由；不转换成 DNS 查询域名匹配"})
        if not exps:
            continue
        tag = "match_" + policy["id"]
        plugins.append({"tag": tag, "type": "domain_set", "args": {"exps": exps}})
        # Each matched branch exits even when its forward fails: no silent fallthrough.
        branch = "branch_" + policy["id"]
        plugins.append({"tag": branch, "type": "sequence", "args": [{"exec": action}, {"exec": "accept"}]})
        condition_entry = "condition_" + policy["id"]
        plugins.append({"tag": condition_entry, "type": "sequence", "args": [{"matches": "qname $" + tag, "exec": "goto " + branch}, {"exec": "goto " + entry}]})
        entry = condition_entry
    chain = [{"exec": "$cache"}, {"matches": "has_resp", "exec": "accept"}, {"exec": "goto " + entry}]
    plugins.append({"tag": "main", "type": "sequence", "args": chain})
    for transport in ("udp", "tcp"):
        plugins.append({"tag": transport + "_listener", "type": transport + "_server", "args": {"entry": "main", "listen": f"127.0.0.1:{s['dns_port']}"}})
    return {"log": {"level": "info"}, "plugins": plugins}, diagnostics


def compile_bundle(value, proxy="mihomo", group_mode_overrides=None, rule_output="inline", component="both", *, dns_engine=None):
    if component not in {"both", "proxy", "dns"}:
        raise ConfigError("component: 请选择 proxy、dns 或 both")
    if rule_output not in {"remote", "local", "inline"}:
        raise ConfigError("规则输出方式无效")
    if component == "dns" and group_mode_overrides:
        raise ConfigError("DNS 独立导出不需要节点组兼容转换")
    from .dns_engine import engine_for
    engine = engine_for(value) if dns_engine is None else dns_engine
    if engine not in {None, "localdns", "mosdns"}:
        raise ConfigError("DNS 内核无效")
    if engine == "localdns" and engine_for(value) != "localdns":
        raise ConfigError("当前空间没有 LocalDNS 原生配置")
    if isinstance(value, dict) and "mosdns" in value and component == "dns" and engine == "mosdns":
        from .mosdns import bundle as dns_bundle
        # The independent DNS model is validated through Profile even for native
        # proxy drafts with unresolved references. No native rule is translated.
        profile = Profile.for_dns({"schema_version": 1, "fallback": "DIRECT", "settings": value.get("settings", {}), "mosdns": value["mosdns"]})
        return dns_bundle(profile.data, rule_output)
    if isinstance(value, dict) and "mosdns" in value and component == "both" and engine == "mosdns":
        # The compatibility CLI package combines the same two independently
        # rendered components. It must not infer DNS's SOCKS or listener from
        # the proxy base, or substitute the legacy DNS settings.
        proxy_bundle = compile_bundle(value, proxy, group_mode_overrides, rule_output, "proxy")
        dns_bundle = compile_bundle(value, proxy, None, rule_output, "dns", dns_engine="mosdns")
        proxy_bundle["files"].update(dns_bundle["files"])
        manifest = proxy_bundle["manifest"]
        manifest.update(component="both", dns_engine="mosdns", startup_order=[proxy, "mosdns"])
        manifest["versions"].update(dns_bundle["manifest"]["versions"])
        manifest.setdefault("rule_output", {})["dns"] = dns_bundle["manifest"]["rule_output"]["mode"]
        manifest["note"] = "代理与 MosDNS 使用各自独立设置；部署前需检查目标设备接线与可达性。"
        return proxy_bundle
    if isinstance(value, dict) and "native_configuration" in value:
        profile = Profile(value)
        if group_mode_overrides:
            raise ConfigError("原生空间不能自动改写节点组类型")
        from .native_configuration import bundle
        return bundle(profile.native_configuration, proxy, rule_output, component)
    original_groups = {g["id"]: g for g in value.get("groups", [])} if isinstance(value, dict) else {}
    from .composition import resolve_composition
    value, composition = resolve_composition(value, include_proxy=component != "dns")
    adjustments = []
    if group_mode_overrides is not None:
        if proxy != "sing-box" or not isinstance(group_mode_overrides, dict):
            raise ConfigError("兼容选择仅适用于 sing-box，并且需要节点组映射")
        value = deepcopy(value)
        groups = {g["id"]: g for g in value.get("groups", [])}
        for ident, mode in group_mode_overrides.items():
            group = groups.get(ident)
            if group is None and ident in original_groups and original_groups[ident].get("mode") in {"fallback", "load-balance"} and mode == "url-test":
                continue
            if not group or group.get("mode") not in {"fallback", "load-balance"} or mode != "url-test":
                raise ConfigError("仅允许明确选择将故障转移或负载均衡组按自动测速导出")
            adjustments.append({"group": ident, "name": group["name"], "original_mode": group["mode"], "export_mode": mode})
            group["mode"] = mode
    profile = Profile.for_dns(value) if component == "dns" else Profile(value)
    if proxy not in {"mihomo", "sing-box"}:
        raise ConfigError("proxy: 请选择 mihomo 或 sing-box")
    if component == "dns":
        # DNS uses its own saved SOCKS endpoint. Do not compile proxy groups,
        # enforce a target rule-source format, or require a proxy binary.
        from .rule_output import externalize_dns
        dns, diagnostics = compile_mosdns(profile)
        files = {}
        if rule_output != "inline":
            externalize_dns(dns, files)
        files["mosdns.yaml"] = dns
        files["README.txt"] = "MosDNS 5.3.4 独立配置。\n在配置包目录执行 mosdns start -c mosdns.yaml -d .\n远程上游使用此方案保存的 SOCKS 地址；请确保目标设备能访问该地址。\n规则为导出时快照；订阅更新后需重新导出。导出不启动服务。\n"
        manifest = {"format": "yomesh-bundle-v1", "component": "dns", "proxy": proxy, "dns_engine": "mosdns", "versions": {"mosdns": "5.3.4"}, "listeners": {"dns": f"127.0.0.1:{profile.settings['dns_port']}"}, "dns_socks5": profile.settings.get("dns_socks5") or f"127.0.0.1:{profile.settings['mixed_port']}", "dns_follow_rules": profile.settings.get("dns_follow_rules", True), "rule_output": {"mode": "inline" if rule_output == "inline" else "local", "dns": "inline-snapshot" if rule_output == "inline" else "local-snapshot"}, "diagnostics": diagnostics, "validation": {"status": "not-run"}, "startup_order": ["mosdns"], "note": "只检查 MosDNS 插件加载与临时监听；未验证外部上游或 SOCKS 连通性。"}
        if composition is not None:
            manifest["composition"] = composition
        return {"files": files, "manifest": manifest}
    from .base_config import effective_settings, apply_base
    profile.settings, profile.native_socks_address = effective_settings(profile, proxy, require_socks=component == "both")
    from .rule_output import rule_assets, externalize_dns
    definitions, files, assets = rule_assets(profile, proxy, rule_output)
    native = compile_mihomo(profile, rule_output != "inline") if proxy == "mihomo" else compile_singbox(profile, rule_output != "inline")
    if definitions:
        if proxy == "mihomo":
            native["rule-providers"] = definitions
        else:
            native["route"]["rule_set"] = list(definitions.values())
    native = apply_base(profile, proxy, native)
    diagnostics = []
    if component == "both":
        dns, diagnostics = compile_mosdns(profile)
        if rule_output != "inline":
            externalize_dns(dns, files)
        files["mosdns.yaml"] = dns
    files[proxy + (".yaml" if proxy == "mihomo" else ".json")] = native
    bundle = {
        "files": files,
        "manifest": {"format": "yomesh-bundle-v1", "proxy": proxy, "versions": {"mihomo": "1.19.32", "sing-box": "1.14.3", "mosdns": "5.3.4"}, "listeners": {"proxy": f"127.0.0.1:{profile.settings['mixed_port']}", "dns": f"127.0.0.1:{profile.settings['dns_port']}"}, "names": {profile.tag(i["id"]): i["name"] for kind in ("nodes", "groups") for i in profile.data[kind]}, "inbounds": [{"id": i["id"], "name": i["name"], "protocol": i["protocol"], "listen": i["listen"], "port": i["port"], "plan": i["plan"]} for i in profile.inbounds if i.get("enabled", True)], "compatibility_choices": adjustments, "diagnostics": diagnostics, "routing_usage": Profile.routing_usage(profile.data), "validation": {"status": "not-run"}, "startup_order": ["proxy", "mosdns"], "note": "配置结构与内核加载验证不代表远程节点可连通；两个代理内核选择其一运行。"},
    }
    if composition is not None:
        bundle["manifest"]["composition"] = composition
    bundle["manifest"]["rule_output"] = {
        "mode": rule_output, "assets": assets,
        "dns": "inline-snapshot" if rule_output == "inline" else "local-snapshot",
        "notes": ["匹配器直接写入代理配置；只打包当前方案和已启用入站方案引用的规则集。"] + (
            ["原始订阅由代理内核按间隔更新；本地规则集随包提供文件。每日定时与请求超时仅用于 YoMesh 拉取。",
             "MosDNS 使用导出时的域名快照，代理订阅更新后需重新导出配套 DNS。",
             "原生检查验证配置结构和本地规则；远程规则在内核运行时下载，后续内容与网络可达性不在检查范围内。"] if rule_output == "remote" else []),
    }
    bundle["manifest"]["listeners"]["proxy"] = profile.native_socks_address
    bundle["manifest"]["base_config"] = {"target": proxy, "custom": bool(value.get("base_configs", {}).get(proxy, "").strip()), "merge": "基础配置覆盖默认基础字段；节点、节点组、分流规则与绑定入站由 Profile 生成"}
    bundle["manifest"]["component"] = component
    if component == "proxy":
        manifest = bundle["manifest"]
        manifest["versions"] = {proxy: manifest["versions"][proxy]}
        manifest["startup_order"] = [proxy]
        manifest["listeners"].pop("dns", None)
        manifest["rule_output"].pop("dns", None)
        manifest["rule_output"]["notes"] = [note for note in manifest["rule_output"]["notes"] if not note.startswith("MosDNS")]
        manifest["note"] = "代理配置独立导出；内置 DNS 按基础配置保留，引用外部 DNS 时需另行部署。原生检查不代表远程节点或 DNS 可连通。"
        files["README.txt"] = manifest["note"] + "\n保留 rules/ 目录（如有）。此包不包含 MosDNS；需要时在 DNS 配置中单独导出。\n"
    return bundle
