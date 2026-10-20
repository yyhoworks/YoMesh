"""Convert a read-only Match Router snapshot without changing routing meaning."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from .model import ConfigError, Profile
from .subscriptions import parse_subscription
from .workspace import clean_source


def seconds(value, default=300):
    if not value:
        return default
    parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value)
    if not parts or ''.join(number + unit for number, unit in parts) != value:
        raise ConfigError("MR 时间格式尚未支持")
    return sum(float(number) * {"ms": .001, "s": 1, "m": 60, "h": 3600}[unit] for number, unit in parts)


def prepare_migration(snapshot, workspace, selected="proxy"):
    profile = deepcopy(workspace["profile"])
    report = {"source": "match-router", "selected_plan": selected, "notes": [], "node_groups": [], "plans": []}
    source_ids = {}
    existing_sources = {s["name"]: s for s in profile["sources"]}
    for record in snapshot["sources"]:
        existing = existing_sources.get(record["name"])
        ident = existing["id"] if existing else "mr_s_" + str(record["id"])
        source_ids[record["id"]] = ident
        minutes = record.get("auto_update_interval_minutes") or 1440
        if minutes % 60:
            raise ConfigError("订阅更新间隔小于小时精度，需先扩展调度支持")
        source = clean_source({"id": ident, "name": record["name"], "url": record["url"], "format": record["type"], "user_agent": record["user_agent"] or "clash.meta", "enabled": bool(record["enabled"]), "include": record["include_regex"], "exclude": record["exclude_regex"], "disabled_protocols": json.loads(record["excluded_protocols"] or "[]"), "auto_update": bool(record["auto_update_enabled"]), "interval_hours": minutes // 60, "test_url": record["speed_test_url"] or "https://www.gstatic.com/generate_204", "test_timeout_ms": int(seconds(record["speed_test_timeout"], 3) * 1000)}, existing)
        if existing:
            profile["sources"][profile["sources"].index(existing)] = source
        else:
            profile["sources"].append(source)
    by_source = {s["id"]: s for s in profile["sources"]}
    imported_nodes, node_ids = [], {}
    for record in snapshot["nodes"]:
        if record.get("bind_interface") or record.get("dialer_proxy_node_id") or record.get("dialer_proxy_node_group_id") or record.get("dns_upstream_id"):
            raise ConfigError("节点包含额外的绑定/链式/DNS 设置，不能静默丢弃：" + record["name"])
        source = source_ids.get(record.get("source_subscription_id"), "manual")
        parsed, stats = parse_subscription(record["url"], {"id": source, "format": "uri"})
        if len(parsed) != 1 or stats["skipped"]:
            raise ConfigError("节点无法完整解析：" + record["name"])
        node = parsed[0]
        matches = [n for n in profile["nodes"] if n.get("source") == source and n["name"] == record["name"]]
        if len(matches) > 1:
            raise ConfigError("存在同名节点，不能猜测引用：" + record["name"])
        if matches:
            node["id"] = matches[0]["id"]
        node.update(name=record["name"], enabled=bool(record["enabled"]))
        node_ids[record["id"]] = node["id"]
        imported_nodes.append(node)
    source_set = set(source_ids.values())
    old_source_nodes = {n["id"] for n in profile["nodes"] if n.get("source") in source_set}
    profile["nodes"] = [n for n in profile["nodes"] if n.get("source") not in source_set] + imported_nodes
    lost = old_source_nodes - {n["id"] for n in imported_nodes}
    report["replaced_filtered_nodes"] = len(lost)
    group_ids = {g["id"]: "mr_g_" + str(g["id"]) for g in snapshot["node_groups"]}
    groups = []
    for record in snapshot["node_groups"]:
        if not record["enabled"]:
            raise ConfigError("停用节点组需要单独的草稿语义，不能当作启用组迁移")
        if record.get("speed_test_url") or record.get("speed_test_timeout"):
            raise ConfigError("节点组有自定义测速参数，需先补齐对应字段")
        mode = {"manual": "select", "urltest": "url-test", "fallback": "fallback", "loadbalance": "load-balance"}.get(record["type"])
        if not mode:
            raise ConfigError("未支持的节点组方式：" + record["type"])
        group = {"id": group_ids[record["id"]], "name": record["name"], "mode": mode, "sources": [source_ids[v["subscription_id"]] for v in snapshot["group_sources"] if v["node_group_id"] == record["id"]], "nodes": [node_ids[v["node_id"]] for v in snapshot["group_nodes"] if v["node_group_id"] == record["id"]], "groups": [group_ids[v["child_group_id"]] for v in snapshot["group_groups"] if v["parent_group_id"] == record["id"]], "include": record["include_regex"] or "", "exclude": record["exclude_regex"] or "", "interval": int(seconds(record["interval"])), "tolerance": round(seconds(record["tolerance"], .05) * 1000)}
        if record.get("member_order"):
            group["order"] = [(node_ids if v["kind"] == "node" else group_ids)[v["id"]] for v in json.loads(record["member_order"])]
        if record.get("selected_node_id"):
            group["selected"] = node_ids[record["selected_node_id"]]
        elif record.get("selected_sub_group_id"):
            group["selected"] = group_ids[record["selected_sub_group_id"]]
        groups.append(group)
    profile["groups"] = [g for g in profile["groups"] if g["id"] not in set(group_ids.values())] + groups
    for group in profile["groups"]:
        group["nodes"] = [n for n in group.get("nodes", []) if n not in lost]
        if group.get("selected") in lost:
            raise ConfigError("原工作区的手选节点将被过滤，请先修改该组")
        # Keep the user's unfinished Hong Kong group as a useful alias, and
        # retain its original definition in the full before-import backup.
        if group["name"] == "香港代理" and not any(group.get(k) for k in ("sources", "nodes", "groups")) and 2 in group_ids:
            group["groups"] = [group_ids[2]]
            report["notes"].append("原有香港代理空组保留，成员引用迁入的香港节点组")
    default = snapshot["default_outbound"][0]

    def target(rule):
        if rule["action"] == "direct":
            return "DIRECT"
        if rule["action"] in {"reject", "block"}:
            return "REJECT"
        if rule["action"] != "proxy":
            raise ConfigError("未支持的 MR 动作：" + rule["action"])
        kind = rule.get("target_kind")
        if kind == "default":
            return group_ids[default["node_group_id"]] if default["target_kind"] == "nodeGroup" else node_ids[default["node_id"]]
        if kind == "nodeGroup":
            return group_ids[rule["target_node_group_id"]]
        if kind == "node":
            return node_ids[rule["target_node_id"]]
        raise ConfigError("未支持的 MR 出口类型")

    provider = snapshot["provider_entries"]
    subscriptions = {v["id"]: v for v in snapshot["rule_subscriptions"]}
    library = {v["id"]: v for v in profile["library"]}
    plans = []
    for device in snapshot["device_groups"]:
        plan = {"id": "mr_" + device["name"], "name": device["name"], "policies": []}
        for rule in [r for r in snapshot["rules"] if r["device_group_id"] == device["id"]]:
            if rule.get("effective_time") not in {None, "", "null"} or rule.get("dns_action"):
                raise ConfigError("存在定时或独立 DNS 动作，不能静默改变：" + rule["name"])
            if rule["is_fallback"]:
                if not rule["enabled"]:
                    raise ConfigError("停用兜底无法等价迁移")
                plan["fallback"] = target(rule)
                continue
            condition_groups = [g for g in snapshot["condition_groups"] if g["rule_id"] == rule["id"]]
            if len(condition_groups) > 1 and rule["group_operator"] != "or":
                raise ConfigError("含 AND 条件组，需要逻辑规则支持：" + rule["name"])
            entries = []
            for cg in condition_groups:
                conditions = [c for c in snapshot["conditions"] if c["group_id"] == cg["id"]]
                if len(conditions) > 1 and cg["operator"] != "or":
                    raise ConfigError("含 AND 条件，需要逻辑规则支持：" + rule["name"])
                for condition in conditions:
                    kind = condition["kind"]
                    if kind == "provider_rule_set":
                        entries.extend(provider[condition["rule_set_id"]])
                    elif kind == "rule_subscription":
                        entries.extend(subscriptions[condition["rule_subscription_id"]]["content_surge"].splitlines())
                    elif kind == "matcher":
                        match = {"domain": "DOMAIN", "domain_suffix": "DOMAIN-SUFFIX", "domain_keyword": "DOMAIN-KEYWORD", "domain_regex": "DOMAIN-REGEX", "ip_cidr": "IP-CIDR", "ip_cidr6": "IP-CIDR6"}.get(condition["matcher_type"])
                        if not match:
                            raise ConfigError("未支持的匹配器：" + condition["matcher_type"])
                        entries.extend(match + "," + line.strip() for line in condition["value"].splitlines() if line.strip())
                    else:
                        raise ConfigError("未支持的规则来源：" + kind)
            entries = list(dict.fromkeys(v.strip() for v in entries if v.strip() and not v.lstrip().startswith("#")))
            if not entries:
                raise ConfigError("不能用空内容替代规则：" + rule["name"])
            ident = "mr_l_" + hashlib.sha256(json.dumps(entries, ensure_ascii=False).encode()).hexdigest()[:20]
            library[ident] = {"id": ident, "name": rule["name"], "kind": "classical", "entries": entries, "note": "来自 MR 的规则内容快照，保留原匹配类型"}
            plan["policies"].append({"id": "mr_p_" + rule["id"], "name": rule["name"], "type": "RULE-SET", "value": ident, "target": target(rule), "enabled": bool(rule["enabled"])})
        if "fallback" not in plan:
            raise ConfigError("规则方案缺少兜底：" + plan["name"])
        plans.append(plan)
        report["plans"].append({"name": plan["name"], "policies": len(plan["policies"]), "fallback": plan["fallback"]})
    active = next((p for p in plans if p["name"] == selected), None)
    if not active:
        raise ConfigError("选择的规则方案不存在")
    profile.update(library=list(library.values()), routing_plans=plans, active_plan=active["id"], policies=deepcopy(active["policies"]), fallback=active["fallback"])
    bindings = {v["inbound_id"]: v["device_group_id"] for v in snapshot["inbound_bindings"]}
    plan_ids = {v["id"]: "mr_" + v["name"] for v in snapshot["device_groups"]}
    default_plan = next(plan_ids[v["id"]] for v in snapshot["device_groups"] if v["is_default"])
    inbounds = []
    for record in snapshot["inbounds"]:
        if record["type"] == "tproxy":
            report["notes"].append("MR 的 tproxy 系统接管保留在原设备；不在群晖自动建立透明代理")
            continue
        listen, separator, port = record["listen"].rpartition(":")
        if not separator:
            raise ConfigError("入站监听格式不支持")
        inbound = {"id": "mr_i_" + str(record["id"]), "name": record["name"], "protocol": record["type"], "listen": listen.strip("[]"), "port": int(port), "enabled": bool(record["enabled"]), "sniff": bool(record["sniff"]), "plan": plan_ids[bindings[record["id"]]] if record["id"] in bindings else default_plan, "home": record["name"].startswith("backhome")}
        if record["users"]:
            raise ConfigError("入站含独立多用户，需要先核对用户结构")
        if record["protocol_password"]:
            inbound["password"] = record["protocol_password"]
        if record["type"] == "shadowsocks":
            inbound["method"] = record["ss_method"]
        if record.get("certificate_pem"):
            inbound.update(certificate=record["certificate_pem"], private_key=record["private_key_pem"])
        if record["type"] == "hysteria2":
            inbound.update(alpn=json.loads(record["hy2_alpn"] or "[]"), up_mbps=record["hy2_up_mbps"], down_mbps=record["hy2_down_mbps"])
            if record["hy2_obfs"]:
                inbound["obfs"] = {"type": record["hy2_obfs"], "password": record["hy2_obfs_password"]}
        inbounds.append(inbound)
    profile["inbounds"] = inbounds
    validated = Profile(profile)
    for record in snapshot["node_groups"]:
        group = group_ids[record["id"]]
        members = validated.members(group)
        report["node_groups"].append({"id": group, "name": record["name"], "mode": next(g["mode"] for g in groups if g["id"] == group), "members": len(members)})
    report.update(nodes=len(imported_nodes), library=len(library), rule_entries=sum(len(l["entries"]) for l in library.values()), inbounds=[{"name": i["name"], "protocol": i["protocol"], "port": i["port"], "plan": i["plan"], "home": i["home"]} for i in inbounds])
    return {"profile": profile, "routing_plans": plans, "active_plan": active["id"], "report": report}
