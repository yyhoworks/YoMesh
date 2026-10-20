"""Independent MosDNS 5.3.4 model, renderer and rule-source snapshots.

Target fields are based on the v5.3.4 plugin Args, never LocalDNS extensions.
"""
from copy import deepcopy
import ipaddress
import re
from urllib.parse import urlsplit

from .model import ConfigError

GROUPS = {"local": "国内上游", "remote": "国外上游", "ecs": "国外 ECS", "proxy": "Mihomo DNS"}
CACHE = {"enabled": True, "size": 1024, "lazy_cache_ttl": 0, "persist": False, "dump_interval": 600}


def defaults(settings=None):
    s = settings or {}
    return {"listen": "127.0.0.1", "port": s.get("dns_port", 1053), "udp": True, "tcp": True,
            "api": "", "log_level": "info", "bootstrap": s.get("bootstrap", "223.5.5.5"),
            "default": s.get("dns_default", "remote"), "cache": deepcopy(CACHE),
            "groups": {k: {"enabled": k in {"local", "remote"}, "concurrent": 1, "ecs": "", "cache": {**CACHE, "enabled": False},
                            "upstreams": [{"tag": "", "addr": s.get("local_upstream", "223.5.5.5") if k == "local" else s.get("remote_upstream", "tcp://1.1.1.1") if k in {"remote", "ecs"} else "127.0.0.1:1053",
                                           "dial_addr": "", "socks5": (s.get("dns_socks5") or "127.0.0.1:" + str(s.get("mixed_port", 17890))) if k in {"remote", "ecs"} else "", "enabled": True}]}
                       for k in GROUPS},
            "block_aaaa": False, "blocked_qtypes": [], "ttl_min": 0, "ttl_max": 0,
            "hosts": [], "rules": [], "fallback": {"enabled": False, "primary": "local", "secondary": "remote", "threshold": 500, "always_standby": False, "response_ips": [], "sources": []},
            "mode": "form", "custom_config": None, "migration": None}


def _error(message):
    raise ConfigError("MosDNS：" + message)


def _keys(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        _error(name + "包含未知字段或不是对象；完整原生配置请使用高级编辑")


def _number(value, low, high, name):
    if type(value) is not int or not low <= value <= high:
        _error(name + f"需要 {low}–{high} 的整数")


def _boolean(value, name):
    if type(value) is not bool:
        _error(name + "需要布尔值")


def _lines(value, name, limit=250000):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or not v.strip() or '\n' in v or '\r' in v for v in value):
        _error(name + "需要非空单行文本列表")


def _endpoint(value, name, optional=False):
    if value == "" and optional:
        return
    try:
        u = urlsplit("//" + value)
        ipaddress.ip_address(u.hostname)
        if not u.port or u.username is not None or u.path or u.query or u.fragment:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        _error(name + "需要 IP:端口（IPv6 使用 [地址]:端口）")


def _address(value, name):
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        _error(name + "地址无效")
    if "://" in value:
        u = urlsplit(value)
        if u.scheme not in {"udp", "tcp", "tls", "https", "quic"} or not u.hostname or u.username or u.password or u.query or u.fragment:
            _error(name + "需要 UDP / TCP / DoT / DoH / DoQ 地址，不支持地址内凭据或查询参数")
    else:
        try:
            ipaddress.ip_address(value)
        except ValueError:
            _endpoint(value, name)


def _cache(value, name):
    _keys(value, CACHE, name)
    c = {**CACHE, **value}
    for k in ("enabled", "persist"):
        _boolean(c[k], name + k)
    _number(c["size"], 1, 2000000, name + "容量")
    _number(c["lazy_cache_ttl"], 0, 2592000, name + "过期保留时间")
    _number(c["dump_interval"], 60, 86400, name + "落盘间隔")
    return c


def domain_expression(value):
    if value.startswith(("full:", "domain:", "keyword:", "regexp:")):
        prefix, content = value.split(":", 1)
        if not content:
            _error("域名表达式内容不能为空")
        if prefix == "regexp":
            try:
                re.compile(content)
            except re.error:
                _error("域名正则表达式无效")
        elif prefix in {"full", "domain"}:
            from .model import check_domain
            check_domain(content, "MosDNS 域名")
        return value
    from .model import check_domain
    suffix = value.startswith(("+.", "."))
    content = value[2:] if value.startswith("+.") else value[1:] if suffix else value
    check_domain(content, "MosDNS 域名")
    return ("domain:" if suffix else "full:") + content


def checked(value, settings=None):
    base = defaults(settings)
    _keys(value, base, "配置")
    cfg = {**base, **deepcopy(value)}
    migration = cfg["migration"]
    if migration is not None:
        _keys(migration, {"source", "reviewed", "items"}, "迁移报告")
        if migration.get("source") != "LocalDNS" or not isinstance(migration.get("items"), list) or len(migration["items"]) > 512:
            _error("迁移报告来源或条目无效")
        _boolean(migration.get("reviewed"), "迁移报告确认状态")
        for item in migration["items"]:
            _keys(item, {"name", "detail", "status"}, "迁移条目")
            if item.get("status") not in {"mapped", "review", "unsupported"} or any(not isinstance(item.get(k), str) or not item[k].strip() or len(item[k]) > limit for k, limit in (("name", 100), ("detail", 1000))):
                _error("迁移条目内容无效")
    if cfg["mode"] not in {"form", "custom"}:
        _error("编辑模式无效")
    if cfg["mode"] == "custom":
        raw = cfg["custom_config"]
        if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), list) or not raw["plugins"]:
            _error("完整配置需要非空 plugins 列表")
        return cfg
    try:
        ipaddress.ip_address(cfg["listen"])
        ipaddress.ip_address(cfg["bootstrap"])
    except (ValueError, TypeError):
        _error("监听和引导 DNS 需要纯 IP")
    _number(cfg["port"], 1, 65535, "监听端口")
    for k in ("udp", "tcp", "block_aaaa"):
        _boolean(cfg[k], k)
    if not (cfg["udp"] or cfg["tcp"]):
        _error("至少启用 UDP 或 TCP 监听")
    _endpoint(cfg["api"], "API 监听", optional=True)
    if cfg["api"] and cfg["tcp"] and urlsplit("//" + cfg["api"]).port == cfg["port"]:
        _error("API 和 DNS TCP 监听不能使用同一个端口")
    if cfg["log_level"] not in {"debug", "info", "warn", "error"}:
        _error("日志级别无效")
    cfg["cache"] = _cache(cfg["cache"], "主缓存")
    _keys(cfg["groups"], GROUPS, "上游组")
    groups = cfg["groups"] = {**base["groups"], **cfg["groups"]}
    for key, g in groups.items():
        _keys(g, base["groups"][key], GROUPS[key])
        g = groups[key] = {**base["groups"][key], **g}
        _boolean(g["enabled"], GROUPS[key])
        _number(g["concurrent"], 1, 16, "并发数")
        g["cache"] = _cache(g["cache"], GROUPS[key] + "缓存")
        if not isinstance(g["ecs"], str):
            _error("ECS 需要 IP 网段或留空")
        if g["ecs"]:
            try:
                network = ipaddress.ip_network(g["ecs"], strict=False)
                if network.prefixlen == 0:
                    raise ValueError()
            except ValueError:
                _error("ECS 需要有效且非 /0 的 IP 网段")
        if not isinstance(g["upstreams"], list) or len(g["upstreams"]) > 64:
            _error("每组最多 64 个上游")
        for u in g["upstreams"]:
            _keys(u, {"tag", "addr", "dial_addr", "socks5", "enabled"}, "上游")
            _boolean(u.get("enabled", True), "上游启用状态")
            if not isinstance(u.get("tag", ""), str) or len(u.get("tag", "")) > 100:
                _error("上游名称最多 100 字符")
            _address(u.get("addr"), "上游")
            if g["enabled"] and u.get("enabled", True):
                self_loop = False
                try:
                    try:
                        host, port = str(ipaddress.ip_address(u["addr"])), 53
                    except ValueError:
                        endpoint = urlsplit(u["addr"] if "://" in u["addr"] else "//" + u["addr"])
                        host, port = endpoint.hostname, endpoint.port or 53
                    loopback = ipaddress.ip_address(host).is_loopback
                    listen = ipaddress.ip_address(cfg["listen"])
                    self_loop = port == cfg["port"] and (host == cfg["listen"] or (loopback and (listen.is_loopback or listen.is_unspecified)))
                except (ValueError, TypeError):
                    pass  # Hostname upstreams are checked by the target core.
                if self_loop:
                    _error("上游指向 MosDNS 自身监听地址，会形成解析循环")
            _endpoint(u.get("socks5", ""), "SOCKS 地址", optional=True)
            if u.get("dial_addr"):
                _address(u["dial_addr"], "拨号地址")
                if "://" in u["dial_addr"]:
                    _error("拨号地址需要 IP 或 IP:端口")
            if u.get("socks5") and not u["addr"].startswith(("tcp://", "tls://", "https://")):
                _error("SOCKS 上游需使用 TCP / DoT / DoH")
        if g["enabled"] and not any(u.get("enabled", True) for u in g["upstreams"]):
            _error(GROUPS[key] + "至少需要一个启用的上游")
    def target(name):
        if name not in groups or not groups[name]["enabled"]:
            _error("所选上游组未启用")
    fb = cfg["fallback"] = {**base["fallback"], **cfg["fallback"]} if isinstance(cfg["fallback"], dict) else {}
    _keys(fb, base["fallback"], "回退")
    if not fb:
        _error("回退需要对象")
    for k in ("enabled", "always_standby"):
        _boolean(fb[k], "回退" + k)
    _number(fb["threshold"], 1, 60000, "回退等待毫秒")
    _lines(fb["response_ips"], "回退响应 IP")
    for n in fb["response_ips"]:
        try:
            ipaddress.ip_network(n, strict=False)
        except ValueError:
            _error("回退响应 IP 需要 CIDR")
    if not isinstance(fb["sources"], list) or len(fb["sources"]) > 64:
        _error("最多 64 个响应 IP 来源")
    source_ids = set()
    for s in fb["sources"]:
        _keys(s, {"id", "name", "enabled", "source", "entries"}, "响应 IP 来源")
        if not isinstance(s.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", s["id"]) or s["id"] in source_ids:
            _error("响应 IP 来源 ID 无效或重复")
        source_ids.add(s["id"])
        if not isinstance(s.get("name"), str) or not s["name"].strip() or len(s["name"]) > 100:
            _error("响应 IP 来源需要名称")
        _boolean(s.get("enabled", True), "响应 IP 来源状态")
        from .rule_sources import clean_source
        s["source"] = clean_source(s.get("source", {}))
        _lines(s.get("entries", []), "响应 IP 来源内容")
        for n in s.get("entries", []):
            try:
                ipaddress.ip_network(n, strict=False)
            except ValueError:
                _error("响应 IP 来源包含无效 CIDR")
        if s.get("enabled", True) and not s.get("entries"):
            _error("启用的响应 IP 来源需要先读取有效快照")
    if fb["enabled"]:
        target(fb["primary"]); target(fb["secondary"])
        if fb["primary"] == fb["secondary"]:
            _error("主上游与备用上游不能相同")
    if cfg["default"] == "fallback":
        if not fb["enabled"]:
            _error("请先启用失败回退")
    else:
        target(cfg["default"])
    for k in ("ttl_min", "ttl_max"):
        _number(cfg[k], 0, 604800, k)
    if cfg["ttl_max"] and cfg["ttl_min"] > cfg["ttl_max"]:
        _error("TTL 最小值不能大于最大值")
    if not isinstance(cfg["blocked_qtypes"], list):
        _error("查询类型需要整数列表")
    for q in cfg["blocked_qtypes"]:
        _number(q, 1, 65535, "查询类型")
    _lines(cfg["hosts"], "域名重写", 10000)
    for line in cfg["hosts"]:
        fields = line.split()
        if len(fields) < 2:
            _error("重写使用 域名 IP [IP…] 格式")
        domain_expression(fields[0])
        try:
            for ip in fields[1:]:
                ipaddress.ip_address(ip)
        except ValueError:
            _error("重写目标需要 IP")
    if not isinstance(cfg["rules"], list) or len(cfg["rules"]) > 512:
        _error("最多 512 条 DNS 规则")
    seen = set()
    for r in cfg["rules"]:
        _keys(r, {"id", "name", "enabled", "domains", "clients", "qtypes", "action", "source"}, "DNS 规则")
        if not isinstance(r.get("id"), str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", r["id"]) or r["id"] in seen:
            _error("DNS 规则 ID 无效或重复")
        seen.add(r["id"])
        if not isinstance(r.get("name"), str) or not r["name"].strip() or len(r["name"]) > 100:
            _error("DNS 规则需要名称（最多 100 字）")
        _boolean(r.get("enabled", True), "规则启用状态")
        for k in ("domains", "clients"):
            _lines(r.get(k, []), "规则" + k)
        r["domains"] = [domain_expression(v) for v in r.get("domains", [])]
        for v in r.get("clients", []):
            try:
                ipaddress.ip_network(v, strict=False)
            except ValueError:
                _error("客户端条件需要 IP 或 CIDR")
        if not isinstance(r.get("qtypes", []), list):
            _error("查询类型需要列表")
        for q in r.get("qtypes", []):
            _number(q, 1, 65535, "规则查询类型")
        if not any(r.get(k) for k in ("domains", "clients", "qtypes")):
            _error("规则至少需要一个条件，未匹配默认值在监听页设置")
        action = r.get("action")
        if action not in {*GROUPS, "reject", "fallback"}:
            _error("规则动作无效")
        if action == "fallback" and not fb["enabled"]:
            _error("规则使用的回退未启用")
        elif action in GROUPS and r.get("enabled", True):
            target(action)
        if r.get("source"):
            from .rule_sources import clean_source
            r["source"] = clean_source(r["source"])
    if cfg["cache"]["enabled"] and any(r.get("enabled", True) and r.get("clients") for r in cfg["rules"]):
        _error("客户端规则需关闭主缓存并使用各上游独立缓存，避免跨客户端复用解析结果")
    return cfg


def view(value):
    return {"config": checked(value["mosdns"], value.get("settings")) if "mosdns" in value else defaults(value.get("settings")),
            "saved": "mosdns" in value, "legacy_localdns": value.get("native_configuration", {}).get("format") == "mihomo-localdns-v1"}


def compile_config(value):
    c = checked(value)
    if c["mode"] == "custom":
        return deepcopy(c["custom_config"])
    plugins = []
    def add(tag, kind, args):
        plugins.append({"tag": tag, "type": kind, "args": args})
    def cache(tag, options):
        args = {k: options[k] for k in ("size", "lazy_cache_ttl")}
        if options["persist"]:
            args.update(dump_file="./cache/" + tag + ".dump", dump_interval=options["dump_interval"])
        add(tag, "cache", args)
    ttl = [{"exec": f"ttl {c['ttl_min']}-{c['ttl_max']}"}] if c["ttl_min"] or c["ttl_max"] else []
    for key, g in c["groups"].items():
        if not g["enabled"]:
            continue
        upstreams = [{k: u[k] for k in ("addr", "dial_addr", "socks5") if u.get(k)} for u in g["upstreams"] if u.get("enabled", True)]
        add(key + "_forward", "forward", {"upstreams": upstreams, "concurrent": g["concurrent"], "bootstrap": c["bootstrap"]})
        chain = []
        if g["cache"]["enabled"]:
            cache(key + "_cache", g["cache"])
            chain += [{"exec": "$" + key + "_cache"}, {"matches": "has_resp", "exec": "accept"}]
        if g["ecs"]:
            net = ipaddress.ip_network(g["ecs"], strict=False)
            add(key + "_ecs", "ecs_handler", {"send": True, "forward": False, "preset": str(net.network_address), "mask4" if net.version == 4 else "mask6": net.prefixlen})
            chain += [{"exec": "$" + key + "_ecs"}]
        chain += [{"exec": "$" + key + "_forward"}, *ttl]
        add(key + "_dns", "sequence", chain)
    fb = c["fallback"]
    if fb["enabled"]:
        primary = fb["primary"] + "_dns"
        response_ips = list(dict.fromkeys(fb["response_ips"] + [n for s in fb["sources"] if s.get("enabled", True) for n in s["entries"]]))
        if response_ips:
            add("fallback_ips", "ip_set", {"ips": response_ips})
            add("fallback_primary", "sequence", [{"exec": "$" + primary}, {"matches": "!resp_ip $fallback_ips", "exec": "drop_resp"}])
            primary = "fallback_primary"
        add("fallback_dns", "fallback", {"primary": primary, "secondary": fb["secondary"] + "_dns", "threshold": fb["threshold"], "always_standby": fb["always_standby"]})
    # Branches are built before main so references can be resolved by MosDNS.
    rules = []
    for i, r in enumerate(c["rules"]):
        if not r.get("enabled", True):
            continue
        matches = []
        if r.get("domains"):
            add("dns_rule_" + str(i), "domain_set", {"exps": r["domains"]})
            matches.append("qname $dns_rule_" + str(i))
        if r.get("clients"):
            matches.append("client_ip " + " ".join(r["clients"]))
        if r.get("qtypes"):
            matches.append("qtype " + " ".join(map(str, r["qtypes"])))
        action = "reject 3" if r["action"] == "reject" else "$" + r["action"] + "_dns"
        add("dns_branch_" + str(i), "sequence", [{"exec": action}, {"exec": "accept"}])
        rules.append({"matches": matches, "exec": "goto dns_branch_" + str(i)})
    main = []
    blocked = sorted(set(c["blocked_qtypes"] + ([28] if c["block_aaaa"] else [])))
    if blocked:
        main.append({"matches": "qtype " + " ".join(map(str, blocked)), "exec": "reject 0"})
    if c["hosts"]:
        add("dns_hosts", "hosts", {"entries": c["hosts"]})
        main += [{"exec": "$dns_hosts"}, {"matches": "has_resp", "exec": "accept"}]
    if c["cache"]["enabled"]:
        cache("main_cache", c["cache"])
        main += [{"exec": "$main_cache"}, {"matches": "has_resp", "exec": "accept"}]
    main += rules + [{"exec": "$" + c["default"] + "_dns"}, {"exec": "accept"}]
    add("main", "sequence", main)
    addr = ("[" + c["listen"] + "]") if ":" in c["listen"] else c["listen"]
    for transport in ("udp", "tcp"):
        if c[transport]:
            add(transport + "_listener", transport + "_server", {"entry": "main", "listen": addr + ":" + str(c["port"])})
    config = {"log": {"level": c["log_level"]}, "plugins": plugins}
    if c["api"]:
        config["api"] = {"http": c["api"]}
    return config


def bundle(value, rule_output="inline"):
    from .rule_output import externalize_dns
    migration = checked(value["mosdns"])["migration"]
    if migration and not migration["reviewed"]:
        _error("请先在迁移报告中核对停用规则与未迁移项，再确认校验和导出；可以先保存草稿")
    config = compile_config(value["mosdns"])
    files = {}
    if rule_output != "inline" and checked(value["mosdns"])["mode"] != "custom":
        externalize_dns(config, files)
    files["mosdns.yaml"] = config
    # Persistent cache paths have a writable parent in the exported bundle.
    files["cache/.keep"] = ""
    files["README.txt"] = "MosDNS 5.3.4 独立配置。\n在包目录执行 mosdns start -c mosdns.yaml -d .\n规则使用工作台已保存快照；刷新后重新导出。cache/ 需可写。\n未验证目标设备的端口、外部上游或 SOCKS 可达性。\n"
    mode = "native" if checked(value["mosdns"])["mode"] == "custom" else "inline" if rule_output == "inline" else "local"
    if migration:
        files["migration-report.json"] = deepcopy(migration)
    return {"files": files, "manifest": {"format": "yomesh-bundle-v1", "component": "dns", "proxy": "mihomo", "dns_engine": "mosdns", "versions": {"mosdns": "5.3.4"}, "rule_output": {"mode": mode}, "validation": {"status": "not-run"}, "startup_order": ["mosdns"], "diagnostics": [item["name"] + "：" + item["detail"] for item in migration["items"] if item["status"] != "mapped"] if migration else [], "note": "MosDNS 独立配置；原生检查仅验证插件和隔离监听。"}}


def refresh_rule(root, rule):
    from .rule_sources import clean_source, fetch, parse_rules
    source = clean_source(rule.get("source", {}))
    if source["type"] != "url":
        _error("此规则没有远程来源")
    item, _ = parse_rules(fetch(source), source["format"], root)
    prefixes = {"DOMAIN": "full:", "DOMAIN-SUFFIX": "domain:", "DOMAIN-KEYWORD": "keyword:", "DOMAIN-REGEX": "regexp:"}
    domains = []
    for entry in item["entries"]:
        kind, content = entry.split(",", 1)
        if kind not in prefixes:
            _error("DNS 域名来源包含 IP 或其他条件，不能删减后导入；请使用单独域名来源")
        domains.append(prefixes[kind] + content)
    return {**deepcopy(rule), "domains": domains, "source": source}


def refresh_ip_source(root, value):
    from .rule_sources import clean_source, fetch, parse_rules
    source = clean_source(value.get("source", {}))
    if source["type"] != "url":
        _error("响应 IP 来源需要订阅地址")
    item, _ = parse_rules(fetch(source), source["format"], root)
    entries = []
    for entry in item["entries"]:
        kind, content = entry.split(",", 1)
        if kind not in {"IP-CIDR", "IP-CIDR6"}:
            _error("响应 IP 来源包含域名或其他条件，不能部分导入")
        entries.append(str(ipaddress.ip_network(content, strict=False)))
    return {**deepcopy(value), "source": source, "entries": list(dict.fromkeys(entries))}


class MosDNSUpdates:
    """One bounded scheduler for independent spaces, including shared overrides."""
    def __init__(self, root, spaces):
        import threading
        self.root, self.spaces = root, spaces
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True, name="mosdns-rule-updates")

    def _loop(self):
        while not self.stop.wait(15):
            try:
                self.run_due()
            except Exception:
                # Keep the scheduler alive, never emit profiles or source URLs.
                pass

    def run_due(self, now=None):
        import hashlib
        import json
        import time
        now = time.time() if now is None else now
        jobs = []
        with self.spaces.lock:
            for space in self.spaces.state["spaces"]:
                workspace = self.spaces.runtime(space["id"]).workspace
                with workspace.lock:
                    cfg = space.get("overrides", {}).get("mosdns", workspace.state["profile"].get("mosdns"))
                    if not cfg or cfg.get("mode", "form") != "form":
                        continue
                    sources = [(False, r) for r in cfg.get("rules", [])]
                    if cfg.get("fallback", {}).get("enabled"):
                        sources.extend((True, s) for s in cfg.get("fallback", {}).get("sources", []))
                    for is_ip, rule in sources:
                        source = rule.get("source", {})
                        if not rule.get("enabled", True) or source.get("type") != "url" or not source.get("auto_update"):
                            continue
                        signature = hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
                        key = space["id"] + ":" + ("ip:" if is_ip else "") + rule["id"]
                        status = workspace.state.get("mosdns_status", {}).get(key, {})
                        if status.get("signature") == signature and status.get("next_update", 0) > now:
                            continue
                        jobs.append((space["id"], key, deepcopy(rule), signature, is_ip))
                        if len(jobs) >= 2:
                            break
                if len(jobs) >= 2:
                    break
        for ident, key, original, signature, is_ip in jobs:
            if self.stop.is_set():
                return
            status = {"signature": signature, "last_attempt": now}
            try:
                updated = refresh_ip_source(self.root, original) if is_ip else refresh_rule(self.root, original)
                if self.stop.is_set():
                    return
                with self.spaces.lock:
                    snapshot = self.spaces.workspace_snapshot(ident)
                    cfg = snapshot["profile"].get("mosdns")
                    if not cfg or cfg.get("mode", "form") != "form":
                        continue
                    items = cfg["fallback"]["sources"] if is_ip else cfg["rules"]
                    current = next((r for r in items if r["id"] == original["id"]), None)
                    if current != original:
                        continue
                    items[items.index(current)] = updated
                    self.spaces.save_mosdns(cfg, snapshot["revision"], ident)
                from .rule_sources import due_time
                status.update(state="success", last_updated=now, next_update=due_time(updated["source"]), error="")
            except Exception:
                status.update(state="error", next_update=now + 300, error="DNS 来源自动更新失败，保留上次有效快照；可在解析规则中手动重试")
            try:
                workspace = self.spaces.runtime(ident).workspace
                with workspace.lock:
                    workspace.state.setdefault("mosdns_status", {}).setdefault(key, {}).update(status)
                    workspace._write()
            except ConfigError:
                continue
