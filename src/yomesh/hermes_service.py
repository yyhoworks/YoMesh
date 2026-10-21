"""Hermes MCP backend service and Streamable HTTP /mcp JSON-RPC handler."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import ipaddress
import json
import re
from pathlib import Path

from .interaction_tools import diagnose as diagnose_native, node_catalog
from .model import BUILTINS, ConfigError, classical_entry as parse_classical
from .publication import Publications
from .spaces import ConfigurationSpaces


RULE_KINDS = {"DOMAIN", "DOMAIN-SUFFIX", "DOMAIN-KEYWORD", "DOMAIN-REGEX", "IP-CIDR", "IP-CIDR6", "HOST", "HOST-SUFFIX", "HOST-KEYWORD"}


def _normalize_single_entry(raw_entry: str) -> tuple[str, str, str]:
    text = str(raw_entry or "").strip()
    if not text:
        raise ConfigError("请提供规则匹配内容，例如 DOMAIN-SUFFIX,openai.com 或 openai.com")
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if len(parts) == 1:
        val = parts[0]
        try:
            net = ipaddress.ip_network(val, strict=False)
            kind = "IP-CIDR6" if net.version == 6 else "IP-CIDR"
            val = str(net)
        except ValueError:
            if val.startswith("*.") or val.startswith("+."):
                val = val[2:]
            kind = "DOMAIN-SUFFIX"
        normalized = f"{kind},{val}"
        parse_classical(normalized, "entry")
        return kind, val, normalized
    kind = parts[0].upper()
    val = parts[1]
    if kind == "HOST-SUFFIX":
        kind = "DOMAIN-SUFFIX"
    elif kind == "HOST":
        kind = "DOMAIN"
    elif kind == "HOST-KEYWORD":
        kind = "DOMAIN-KEYWORD"
    normalized = f"{kind},{val}"
    parse_classical(normalized, "entry")
    return kind, val, normalized


def _parse_batch_entries(raw_entry: str | list) -> list[tuple[str, str, str]]:
    if isinstance(raw_entry, list):
        tokens = [str(x).strip() for x in raw_entry if str(x).strip()]
    else:
        text = str(raw_entry or "").strip()
        if not text:
            raise ConfigError("请提供规则匹配内容，例如 claude.ai 或 DOMAIN-SUFFIX,openai.com")
        lines = [ln.strip() for ln in re.split(r"[\r\n;、]+", text) if ln.strip()]
        tokens = []
        for line in lines:
            first_part = line.split(",", 1)[0].strip().upper()
            if first_part in RULE_KINDS:
                tokens.append(line)
            else:
                for sub in re.split(r"[,\s]+", line):
                    if sub.strip():
                        tokens.append(sub.strip())
    if not tokens:
        raise ConfigError("请提供至少一条有效域名或 IP")
    return [_normalize_single_entry(tok) for tok in tokens]


def _fuzzy_resolve_target(raw_target: str, candidates: dict[str, str], labels: dict[str, str]) -> str | None:
    q = raw_target.strip()
    if not q:
        return None
    ql = q.lower()
    # 1. Exact match
    if q in candidates:
        return candidates[q]
    if ql in candidates:
        return candidates[ql]
    # 2. Region / alias keywords
    aliases = {
        "香港": ["香港", "hk", "hong kong"],
        "hk": ["香港", "hk", "hong kong"],
        "美国": ["美国", "us", "usa", "united states"],
        "us": ["美国", "us", "usa", "united states"],
        "日本": ["日本", "jp", "japan"],
        "jp": ["日本", "jp", "japan"],
        "新加坡": ["新加坡", "sg", "singapore", "狮城"],
        "sg": ["新加坡", "sg", "singapore", "狮城"],
        "台湾": ["台湾", "tw", "taiwan"],
        "tw": ["台湾", "tw", "taiwan"],
        "代理": ["proxy", "默认代理", "节点选择", "自动选择"],
    }
    search_terms = aliases.get(ql, [ql])
    for key, resolved_id in candidates.items():
        label_lower = labels.get(resolved_id, key).lower()
        for term in search_terms:
            if term in label_lower or label_lower in term:
                return resolved_id
    return None


def _match_entry(kind: str, val: str, query: str, query_ip: ipaddress.IPv4Address | ipaddress.IPv6Address | None) -> bool:
    q = query.lower().strip(".")
    v = val.lower().strip(".")
    if kind == "DOMAIN":
        return q == v
    if kind == "DOMAIN-SUFFIX":
        return q == v or q.endswith("." + v)
    if kind == "DOMAIN-KEYWORD":
        return v in q
    if kind == "DOMAIN-REGEX":
        try:
            return bool(re.search(val, query, re.IGNORECASE))
        except re.error:
            return False
    if kind in {"IP-CIDR", "IP-CIDR6"} and query_ip is not None:
        try:
            net = ipaddress.ip_network(val, strict=False)
            return query_ip.version == net.version and query_ip in net
        except ValueError:
            return False
    return False


MCP_TOOLS = [
    {
        "name": "yomesh_list_spaces",
        "description": "列出 YoMesh 中的所有配置方案（含方案 ID、名称、代理内核、节点/规则统计以及固定订阅链接）。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "yomesh_diagnose_domain",
        "description": "诊断某个域名或 IP 在指定配置方案中命中了哪条分流规则、最终走哪个节点组或直连。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "要诊断的域名或 IP，例如 api.openai.com"},
                "space_id": {"type": "string", "description": "可选：配置方案名称或 ID（如 Proxy客户端）；留空使用当前激活方案"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yomesh_add_rule",
        "description": "向 YoMesh 配置方案添加一条或多条分流规则（默认置顶最高优先级生效，支持分组名模糊匹配如“美国”、“香港”，并自动通知客户端热重载）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "entry": {
                    "type": "string",
                    "description": "域名、IP-CIDR 或规则表达式（支持一次传多个域名，用逗号或空格分隔，如 'claude.ai, cursor.com'）",
                },
                "target": {
                    "type": "string",
                    "description": "目标出口：节点组名称（支持模糊匹配，如 '美国'、'香港'、'US 美国节点'）、DIRECT（直连）或 REJECT（拦截）",
                },
                "rule_name": {"type": "string", "description": "可选：归入的规则名称"},
                "space_id": {"type": "string", "description": "可选：配置方案名称或 ID（如 Proxy客户端）；留空使用当前激活方案"},
            },
            "required": ["entry", "target"],
            "additionalProperties": False,
        },
    },
    {
        "name": "yomesh_test_nodes",
        "description": "查看指定配置方案中的节点列表与延迟，或触发实时节点测速。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "test": {"type": "boolean", "description": "设为 true 时触发真实节点测速；false 仅查看现有节点与延迟"},
                "space_id": {"type": "string", "description": "可选：配置方案名称或 ID"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "yomesh_subscription_link",
        "description": "获取或生成指定配置方案的 Mihomo / sing-box 固定订阅链接（可供客户端或 LocalDNS 拉取）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "proxy": {"type": "string", "enum": ["mihomo", "sing-box"], "description": "代理内核类型（默认 mihomo）"},
                "create": {"type": "boolean", "description": "若为 true 则生成新订阅链接；默认优先返回已有链接"},
                "space_id": {"type": "string", "description": "可选：配置方案名称或 ID"},
            },
            "additionalProperties": False,
        },
    },
]


class HermesService:
    def __init__(self, root: Path, spaces: ConfigurationSpaces, publications: Publications, interactions, controllers=None):
        self.root = Path(root)
        self.spaces = spaces
        self.publications = publications
        self.interactions = interactions
        self.controllers = controllers

    def _resolve_space_id(self, requested: str | None) -> str:
        catalog = self.spaces.snapshot()
        if not requested:
            return catalog["active"]
        req_clean = str(requested).strip()
        for item in catalog["spaces"]:
            if item["id"] == req_clean or item["name"] == req_clean:
                return item["id"]
        for item in catalog["spaces"]:
            if req_clean.lower() in item["name"].lower() or item["name"].lower() in req_clean.lower():
                return item["id"]
        raise ConfigError(f"未找到配置方案：{requested}")

    def spaces_overview(self) -> dict:
        catalog = self.spaces.snapshot()
        controllers = self.controllers.listing()["controllers"] if self.controllers else {}
        rows = []
        for item in catalog["spaces"]:
            links = self.publications.listing(item["id"]).get("links", [])
            rows.append({
                "id": item["id"],
                "name": item["name"],
                "description": item.get("description", ""),
                "active": item["id"] == catalog["active"],
                "native": bool(item.get("native")),
                "proxy": item.get("proxy", "mihomo"),
                "rule_output": item.get("rule_output", "inline"),
                "counts": item.get("counts", {}),
                "valid": item.get("generation", {}).get("valid", True),
                "client_controller": controllers.get(item["id"]),
                "subscription_links": [
                    {"id": l["id"], "proxy": l["proxy"], "url": l["url"], "rule_address": l["rule_address"]}
                    for l in links
                ],
            })
        return {"active_space": catalog["active"], "spaces": rows}

    def diagnose_query(self, request: dict) -> dict:
        space_id = self._resolve_space_id(request.get("space_id") or request.get("space"))
        query = str(request.get("query") or "").strip()
        if not query:
            raise ConfigError("请提供要诊断的域名或 IP")
        snapshot = self.spaces.workspace_snapshot(space_id)
        profile = snapshot["profile"]
        native = profile.get("native_configuration")
        if native:
            result = diagnose_native(native, query, request.get("context"))
            return {"space_id": space_id, "mode": "native", **result}

        selection = profile.get("composition") or {}
        plan_id = selection.get("plan") or profile.get("active_plan")
        plans = profile.get("routing_plans") or []
        plan = next((p for p in plans if p["id"] == plan_id), None)
        policies = plan["policies"] if plan else profile.get("policies", [])
        fallback = selection.get("fallback") or (plan["fallback"] if plan else profile.get("fallback", "DIRECT"))

        selected_policies = selection.get("policies")
        selected_rules = selection.get("rules")
        selected_groups = selection.get("groups")

        lib_by_id = {r["id"]: r for r in profile.get("library", [])}
        group_names = {g["id"]: g["name"] for g in profile.get("groups", [])}
        node_names = {n["id"]: n["name"] for n in profile.get("nodes", [])}

        def target_label(t: str) -> str:
            return group_names.get(t) or node_names.get(t) or t

        try:
            query_ip = ipaddress.ip_address(query)
        except ValueError:
            query_ip = None

        matches = []
        for idx, pol in enumerate(policies):
            if not pol.get("enabled", True):
                continue
            if selected_policies is not None and pol["id"] not in selected_policies:
                continue
            target = pol.get("target", "DIRECT")
            if target in group_names and selected_groups is not None and target not in selected_groups:
                continue
            ptype = pol.get("type")
            pval = pol.get("value", "")
            if ptype == "RULE-SET":
                if selected_rules is not None and pval not in selected_rules:
                    continue
                lib = lib_by_id.get(pval)
                if not lib or not lib.get("enabled", True):
                    continue
                for entry in lib.get("entries", []):
                    ekind, _, eval_str = entry.partition(",")
                    if _match_entry(ekind, eval_str, query, query_ip):
                        matches.append({
                            "order": idx + 1,
                            "policy_id": pol["id"],
                            "policy_name": pol.get("name", lib["name"]),
                            "rule_name": lib["name"],
                            "matched_entry": entry,
                            "target": target,
                            "target_name": target_label(target),
                        })
                        break
            else:
                if _match_entry(ptype, pval, query, query_ip):
                    matches.append({
                        "order": idx + 1,
                        "policy_id": pol["id"],
                        "policy_name": pol.get("name", f"{ptype},{pval}"),
                        "matched_entry": f"{ptype},{pval}",
                        "target": target,
                        "target_name": target_label(target),
                    })

        first = matches[0] if matches else {
            "order": len(policies) + 1,
            "policy_id": "fallback",
            "policy_name": "兜底策略 (MATCH)",
            "matched_entry": f"MATCH,{fallback}",
            "target": fallback,
            "target_name": target_label(fallback),
        }
        return {
            "space_id": space_id,
            "mode": "portable",
            "query": query,
            "winner": first,
            "all_matches": matches[:10],
            "available_targets": ["DIRECT", "REJECT", *group_names.values()],
        }

    def add_rule(self, request: dict) -> dict:
        space_id = self._resolve_space_id(request.get("space_id") or request.get("space"))
        entries = _parse_batch_entries(request.get("entries") or request.get("entry") or "")
        raw_target = str(request.get("target") or "").strip()
        if not raw_target:
            raise ConfigError("请指定规则出口（例如节点组名称、DIRECT 或 REJECT）")

        snapshot = self.spaces.workspace_snapshot(space_id)
        revision = snapshot["revision"]
        profile = deepcopy(snapshot["profile"])
        native = profile.get("native_configuration")

        if native:
            groups = [g["name"] for g in native.get("mihomo", {}).get("proxy-groups", []) if isinstance(g, dict) and g.get("name")]
            nodes = [n["name"] for n in native.get("mihomo", {}).get("proxies", []) if isinstance(n, dict) and n.get("name")]
            valid_targets = {"DIRECT": "DIRECT", "直连": "DIRECT", "REJECT": "REJECT", "拦截": "REJECT", "拒绝": "REJECT"}
            labels = {"DIRECT": "DIRECT", "REJECT": "REJECT"}
            for name in (*groups, *nodes):
                valid_targets[name] = name
                valid_targets[name.lower()] = name
                labels[name] = name
            resolved_target = _fuzzy_resolve_target(raw_target, valid_targets, labels)
            if not resolved_target:
                raise ConfigError(f"未找到出口「{raw_target}」；可选出口：{', '.join(['DIRECT', 'REJECT', *groups[:15]])}")

            policies = list(native.get("policies") or [])
            added_rules = []
            # Insert in reverse order at index 0 so all newly added rules sit at the very top of policies
            for kind, val, _ in reversed(entries):
                suffix = ",no-resolve" if kind in {"IP-CIDR", "IP-CIDR6"} else ""
                rule_line = f"{kind},{val},{resolved_target}{suffix}"
                # Remove existing duplicate if any, then place at the top (index 0) for highest priority
                policies = [item for item in policies if item.get("text") != rule_line]
                new_row = {
                    "id": "hm_" + hashlib.sha256(rule_line.encode("utf-8")).hexdigest()[:18],
                    "text": rule_line,
                    "enabled": True,
                }
                policies.insert(0, new_row)
                added_rules.insert(0, rule_line)

            native["policies"] = policies
            self.spaces.save_profile(profile, revision, space_id)
            reload_info = self.controllers.notify_reload(space_id) if self.controllers else {"notified": False}
            return {
                "space_id": space_id,
                "status": "added",
                "mode": "native",
                "position": "top",
                "priority_rank": 1,
                "rule": added_rules[0],
                "rules": added_rules,
                "target": resolved_target,
                "target_name": resolved_target,
                "client_reload": reload_info,
            }

        # Portable space
        target_map = {"DIRECT": "DIRECT", "直连": "DIRECT", "REJECT": "REJECT", "拦截": "REJECT", "拒绝": "REJECT"}
        target_labels = {"DIRECT": "DIRECT", "REJECT": "REJECT"}
        for g in profile.get("groups", []):
            target_map[g["id"]] = g["id"]
            target_map[g["name"]] = g["id"]
            target_map[g["name"].lower()] = g["id"]
            target_labels[g["id"]] = g["name"]
        for n in profile.get("nodes", []):
            target_map[n["id"]] = n["id"]
            target_map[n["name"]] = n["id"]
            target_map[n["name"].lower()] = n["id"]
            target_labels[n["id"]] = n["name"]

        resolved_target = _fuzzy_resolve_target(raw_target, target_map, target_labels)
        if not resolved_target:
            available = ["DIRECT", "REJECT", *(g["name"] for g in profile.get("groups", []))]
            raise ConfigError(f"未找到出口「{raw_target}」；可选出口：{', '.join(available[:15])}")

        rule_name = str(request.get("rule_name") or f"Hermes · {target_labels[resolved_target]}").strip()[:80]
        library = profile.setdefault("library", [])
        lib_item = next((r for r in library if r["name"] == rule_name and not r.get("source")), None)
        normalized_list = [norm for _, _, norm in entries]
        if lib_item is None:
            lid = "l_hm_" + hashlib.sha256((rule_name + "\0" + resolved_target).encode("utf-8")).hexdigest()[:12]
            lib_item = {"id": lid, "name": rule_name, "kind": "classical", "entries": list(normalized_list), "enabled": True}
            library.append(lib_item)
        else:
            for norm in normalized_list:
                if norm not in lib_item["entries"]:
                    lib_item["entries"].append(norm)
            lib_item["enabled"] = True

        selection = profile.get("composition")
        if isinstance(selection, dict) and isinstance(selection.get("rules"), list):
            if lib_item["id"] not in selection["rules"]:
                selection["rules"].append(lib_item["id"])

        def ensure_policy_top(pol_list: list) -> str:
            existing = next(
                (p for p in pol_list if p.get("type") == "RULE-SET" and p.get("value") == lib_item["id"]),
                None,
            )
            if existing:
                existing["target"] = resolved_target
                existing["enabled"] = True
                pol_list.remove(existing)
                pol_list.insert(0, existing)
                return existing["id"]
            pid = "p_hm_" + hashlib.sha256((lib_item["id"] + "\0" + resolved_target).encode("utf-8")).hexdigest()[:10]
            pol_list.insert(0, {
                "id": pid,
                "name": rule_name,
                "type": "RULE-SET",
                "value": lib_item["id"],
                "target": resolved_target,
                "enabled": True,
            })
            return pid

        pid = ensure_policy_top(profile.setdefault("policies", []))
        plans = profile.get("routing_plans") or []
        plan_id = (selection or {}).get("plan") or profile.get("active_plan")
        for plan in plans:
            if plan["id"] == plan_id:
                pid = ensure_policy_top(plan.setdefault("policies", []))
        if isinstance(selection, dict) and isinstance(selection.get("policies"), list):
            if pid in selection["policies"]:
                selection["policies"].remove(pid)
            selection["policies"].insert(0, pid)

        self.spaces.save_profile(profile, revision, space_id)
        reload_info = self.controllers.notify_reload(space_id) if self.controllers else {"notified": False}
        return {
            "space_id": space_id,
            "status": "added",
            "mode": "portable",
            "position": "top",
            "priority_rank": 1,
            "rule_name": rule_name,
            "entry": normalized_list[0],
            "entries": normalized_list,
            "target": target_labels[resolved_target],
            "target_name": target_labels[resolved_target],
            "client_reload": reload_info,
        }

    def nodes_overview(self, request: dict) -> dict:
        space_id = self._resolve_space_id(request.get("space_id") or request.get("space"))
        snapshot = self.spaces.workspace_snapshot(space_id)
        profile = snapshot["profile"]
        statuses = snapshot.get("node_status") or {}
        native = profile.get("native_configuration")
        if native:
            nodes = node_catalog(native)
            if request.get("test") is True and nodes:
                ids = [n["id"] for n in nodes[:50]]
                runtime = self.spaces.runtime(space_id)
                self.interactions.test_nodes(runtime, native, {"ids": ids, "timeout": 5000})
                snapshot = self.spaces.workspace_snapshot(space_id)
                statuses = snapshot.get("node_status") or {}
            return {
                "space_id": space_id,
                "mode": "native",
                "node_count": len(nodes),
                "nodes": [
                    {
                        "id": n["id"],
                        "name": n["name"],
                        "type": n["type"],
                        "provider": n.get("provider", "manual"),
                        "latency_ms": statuses.get(n["id"], {}).get("latency_ms"),
                        "state": statuses.get(n["id"], {}).get("state", "untested"),
                    }
                    for n in nodes[:100]
                ],
            }

        nodes = profile.get("nodes", [])
        groups = profile.get("groups", [])
        if request.get("test") is True and nodes:
            runtime = self.spaces.runtime(space_id)
            runtime.subscriptions.test_nodes([n["id"] for n in nodes[:50]])
            snapshot = self.spaces.workspace_snapshot(space_id)
            statuses = snapshot.get("node_status") or {}
        return {
            "space_id": space_id,
            "mode": "portable",
            "node_count": len(nodes),
            "groups": [{"id": g["id"], "name": g["name"], "mode": g["mode"], "member_count": len(g.get("nodes", []))} for g in groups],
            "nodes": [
                {
                    "id": n["id"],
                    "name": n["name"],
                    "type": n["type"],
                    "latency_ms": statuses.get(n["id"], {}).get("latency_ms"),
                    "state": statuses.get(n["id"], {}).get("state", "untested"),
                }
                for n in nodes[:100]
            ],
        }

    def subscription_link(self, request: dict, default_base_url: str) -> dict:
        space_id = self._resolve_space_id(request.get("space_id"))
        listing = self.publications.listing(space_id)
        links = listing.get("links", [])
        proxy = str(request.get("proxy") or "mihomo").strip()
        if proxy not in {"mihomo", "sing-box"}:
            raise ConfigError("代理类型需为 mihomo 或 sing-box")
        if request.get("create") is True or not links:
            base_url = str(request.get("base_url") or default_base_url).strip()
            revision = self.spaces.workspace_snapshot(space_id)["revision"]
            created = self.publications.create(space_id, {
                "proxy": proxy,
                "base_url": base_url,
                "rule_address": str(request.get("rule_address") or "original"),
                "revision": revision,
            })
            return {"space_id": space_id, "created": True, "link": created}
        matched = next((l for l in reversed(links) if l.get("proxy") == proxy), links[-1])
        return {"space_id": space_id, "created": False, "link": matched, "all_links": links}

    def handle_mcp_rpc(self, msg: dict, default_base_url: str) -> dict | None:
        """Handle Streamable HTTP JSON-RPC 2.0 MCP requests at POST /mcp."""
        req_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}

        if method == "initialize":
            client_proto = params.get("protocolVersion") or "2024-11-05"
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": client_proto,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "yomesh-hermes-mcp", "version": "0.17.2"},
                },
            }
        if method == "notifications/initialized":
            return None
        if method == "ping":
            return {"jsonrpc": "2.0", "id": req_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                if name == "yomesh_list_spaces":
                    res = self.spaces_overview()
                elif name == "yomesh_diagnose_domain":
                    res = self.diagnose_query(args)
                elif name == "yomesh_add_rule":
                    res = self.add_rule(args)
                elif name == "yomesh_test_nodes":
                    res = self.nodes_overview(args)
                elif name == "yomesh_subscription_link":
                    res = self.subscription_link(args, default_base_url)
                else:
                    raise ConfigError(f"未知的 MCP 工具：{name}")
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(res, ensure_ascii=False, indent=2)}],
                        "isError": False,
                    },
                }
            except Exception as err:
                return {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": str(err)}],
                        "isError": True,
                    },
                }
        if req_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return None
