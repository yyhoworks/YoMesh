"""Convert saved LocalDNS snapshots into an explicit, reviewable MosDNS draft.

No network requests, service operations or private-file logging occur here.
The original documents remain the source of truth for unsupported behavior.
"""
import base64
import ipaddress
from .model import ConfigError
from .mosdns import checked, defaults, domain_expression
from .native_configuration import json_document, safe_path
from .rule_sources import clean_source, parse_rules


def import_localdns(profile, root):
    native = profile.get("native_configuration", {})
    if native.get("format") != "mihomo-localdns-v1":
        raise ConfigError("当前空间没有 LocalDNS 原件，不能生成迁移草稿")
    docs, assets = native.get("documents", {}), native.get("assets", {})
    config = defaults(profile.get("settings"))
    items = []

    def note(name, detail, status="review"):
        items.append({"name": name, "detail": detail, "status": status})

    def document(path, default=None):
        text = docs.get("localdns/" + path)
        return json_document(text) if text is not None else default

    def lines(name):
        text = docs.get("localdns/rules/manual/" + name + ".txt", "")
        return [s.strip() for s in text.splitlines() if s.strip() and not s.lstrip().startswith("#")]

    def endpoint(text):
        host, port = text.rsplit(":", 1)
        host = host.strip("[]") or "0.0.0.0"
        ipaddress.ip_address(host)
        return host, int(port)

    service = document("config/localdns.json")
    if not isinstance(service, dict) or not isinstance(service.get("dns"), dict):
        raise ConfigError("LocalDNS 原件缺少有效的服务监听配置")
    listen = service["dns"]
    try:
        config["udp"] = bool(listen.get("udp_listen"))
        config["tcp"] = bool(listen.get("tcp_listen"))
        config["listen"], config["port"] = endpoint(listen.get("udp_listen") or listen.get("tcp_listen"))
        if config["udp"] and config["tcp"] and endpoint(listen["tcp_listen"]) != endpoint(listen["udp_listen"]):
            note("不同的 UDP / TCP 监听", "表单共用一个监听地址，已采用 UDP 地址；原 TCP 地址保留在 LocalDNS 原件中。", "unsupported")
    except (ValueError, TypeError, AttributeError):
        raise ConfigError("LocalDNS 监听格式无法转换，原配置未修改") from None
    config["api"] = service.get("api", {}).get("http", "")
    switches = document("config/settings/switches.json", {})
    overrides = document("config/settings/upstream_overrides.json", {})
    general = document("config/settings/overrides.json", {})
    caches = document("config/cache.json", {})
    config["block_aaaa"] = switches.get("block_aaaa", False)
    config["cache"].update(caches.get("main", {}), enabled=False)
    config["cache"]["persist"] = bool(caches.get("main"))
    mapping = {"local": "domestic", "remote": "foreign", "ecs": "foreign_ecs", "proxy": "foreign_fakeip"}
    for key, source_name in mapping.items():
        original = overrides.get(source_name)
        source = document("config/upstreams/" + ("proxy" if key == "proxy" else source_name) + ".json", {})
        if original is None:
            original = source.get("upstreams", [])
        if not isinstance(original, list) or any(not isinstance(u, dict) for u in original):
            raise ConfigError("LocalDNS 上游原件格式无效，未生成部分迁移结果")
        group = config["groups"][key]
        group["upstreams"] = [{"tag": u.get("tag", ""), "addr": u.get("addr", ""),
                               "dial_addr": u.get("dial_addr", ""), "socks5": u.get("socks5", ""),
                               "enabled": u.get("enabled", True)} for u in original]
        count = sum(bool(u["enabled"]) for u in group["upstreams"])
        group.update(enabled=count > 0, concurrent=max(1, min(16, count)),
                     ecs=source.get("ecs", general.get("ecs", "")) if key == "ecs" else "")
        group["cache"].update(caches.get(source_name, {}), enabled=bool(caches.get(source_name)))
        group["cache"]["persist"] = bool(caches.get(source_name))
        if any(u.get("upstream_query_timeout") for u in original):
            note("上游超时", "MosDNS 5.3.4 没有等价的逐上游超时字段，未伪造该设置。", "unsupported")
    enabled = [key for key, value in config["groups"].items() if value["enabled"]]
    if not enabled:
        raise ConfigError("LocalDNS 原件没有可转换的启用上游")
    config["default"] = "remote" if "remote" in enabled else enabled[0]
    config["hosts"] = lines("rewrite_rules")

    def rule(ident, name, domains, action, clients=None, source=None):
        if not domains and not clients:
            return
        row = {"id": ident, "name": "待核对 · " + name, "enabled": False,
               "domains": [domain_expression(v) for v in domains], "clients": clients or [], "qtypes": [], "action": action}
        if source:
            row["source"] = source
        config["rules"].append(row)

    for filename, name, action in (("block_domains", "手动屏蔽", "reject"), ("direct_domains", "手动直连", "local"),
                                   ("realip_domains", "强制 RealIP", "remote"), ("proxy_domains", "灰名单 / Mihomo DNS", "proxy"),
                                   ("ddns_domains", "DDNS", "local"), ("pcdn_domains", "PCDN 域名（动作待核对）", "reject")):
        rule("ld_" + filename, name, lines(filename), action)
    rule("ld_clients", "客户端名单（策略待核对）", [], "proxy", lines("client_ips"))
    prefixes = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:", "DOMAIN-REGEX": "regexp:"}
    for category, action in (("custom_cn", "local"), ("custom_non_cn", "remote"), ("geosite_cn", "local"), ("geosite_non_cn", "proxy"), ("geoip_cn", None)):
        subscriptions = document("config/subscriptions/" + category + ".json", [])
        if not isinstance(subscriptions, list):
            raise ConfigError("LocalDNS 订阅原件需要列表")
        for index, sub in enumerate(subscriptions):
            if not isinstance(sub, dict):
                raise ConfigError("LocalDNS 订阅原件需要对象")
            name = str(sub.get("name", category))[:70]
            try:
                path = "localdns/" + safe_path(sub.get("files", ""))
                if not path.startswith("localdns/rules/"):
                    raise ConfigError("来源快照路径不在规则目录中")
                body = docs[path].encode() if path in docs else base64.b64decode(assets[path], validate=True)
                fmt = "srs" if path.endswith(".srs") else {"clash_yaml": "clash", "txt": "auto"}.get(sub.get("detected_format"), sub.get("detected_format", "auto"))
                entries = parse_rules(body, fmt, root)[0]["entries"]
                source = clean_source({"type": "url", "url": sub.get("url"), "format": {"clash_yaml": "clash", "txt": "auto"}.get(sub.get("detected_format"), sub.get("detected_format", "auto")),
                                       "auto_update": False, "interval_minutes": int(sub.get("update_interval_hours", 24) * 60)})
                ident = "ld_" + category + "_" + str(index)
                if action is None:
                    cidrs = []
                    for entry in entries:
                        kind, content = entry.split(",", 1)
                        if kind not in {"IP-CIDR", "IP-CIDR6"}:
                            raise ConfigError("IP 来源含其他条件，整条来源未转换")
                        cidrs.append(content)
                    config["fallback"]["sources"].append({"id": ident, "name": name, "enabled": False, "entries": cidrs, "source": source})
                else:
                    domains = []
                    for entry in entries:
                        kind, content = entry.split(",", 1)
                        if kind not in prefixes:
                            raise ConfigError("域名来源含其他条件，整条来源未转换")
                        domains.append(prefixes[kind] + content)
                    rule(ident, name, domains, action, source=source)
            except (ConfigError, KeyError, ValueError, TypeError):
                # Never expose raw parser exceptions, source URLs or credentials.
                note(name, "来源快照缺失、格式不支持或含非等价条件；整条来源未转换，原件完整保留。", "unsupported")
    note("监听、上游与固定解析", "已读取原件中的监听、逐上游启停、拨号地址、SOCKS、ECS、缓存参数与固定解析记录。", "mapped")
    note("分流顺序与客户端策略", "域名规则、客户端名单及响应 IP 来源已保留为停用草稿。请逐项核对动作和优先级后启用；默认上游暂选国外组（不可用时选首个启用组）。")
    note("缓存与来源刷新", "主缓存因客户端路由隔离暂时关闭；组缓存保留。订阅保留地址、格式和间隔，自动刷新暂时关闭。并发暂取启用上游数量。")
    if switches.get("block_special_qtypes"):
        note("特殊查询类型屏蔽", "原件仅提供开关，未提供完整类型清单及返回码；请在缓存与开关表单填写，未猜测启用。", "unsupported")
    for filename, name in (("direct_cidrs", "直连 IP"), ("pcdn_ipv4_rules", "PCDN IPv4 正则"), ("pcdn_ipv6_rules", "PCDN IPv6 正则")):
        if lines(filename):
            note(name, "当前表单没有该响应阶段动作的等价表达，原始条件保留在 LocalDNS 原件中。", "unsupported")
    note("LocalDNS 运行时功能", "动态 FakeIP / RealIP / Nodata、路由缓存、审计、客户端名称、Requery、完整 AdGuard、内存上限与服务管理未等价迁移。Mihomo DNS 仅作为既有外部上游。", "unsupported")
    config["migration"] = {"source": "LocalDNS", "reviewed": False, "items": items}
    config = checked(config)
    return {"settings": config, "report": config["migration"], "summary": {
        "upstreams": sum(len(g["upstreams"]) for g in config["groups"].values()), "hosts": len(config["hosts"]),
        "rules": len(config["rules"]), "ip_sources": len(config["fallback"]["sources"])}}
