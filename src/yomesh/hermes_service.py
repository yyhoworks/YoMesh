"""Hermes MCP backend service for managing YoMesh spaces, rules, nodes, and subscriptions."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import ipaddress
import re
from pathlib import Path

from .interaction_tools import diagnose as diagnose_native, node_catalog
from .model import BUILTINS, ConfigError, Profile, classical_entry as parse_classical
from .publication import Publications
from .spaces import ConfigurationSpaces


def _normalize_rule_entry(raw_entry: str) -> tuple[str, str, str]:
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
            if val.startswith("*."):
                val = val[2:]
            elif val.startswith("+."):
                val = val[2:]
            kind = "DOMAIN-SUFFIX"
        normalized = f"{kind},{val}"
        parse_classical(normalized, "entry")
        return kind, val, normalized
    kind = parts[0].upper()
    val = parts[1]
    if kind in {"HOST-SUFFIX", " Constrained-SUFFIX"}:
        kind = "DOMAIN-SUFFIX"
    elif kind == "HOST":
        kind = "DOMAIN"
    elif kind == "HOST-KEYWORD":
        kind = "DOMAIN-KEYWORD"
    normalized = f"{kind},{val}"
    parse_classical(normalized, "entry")
    return kind, val, normalized


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


class HermesService:
    def __init__(self, root: Path, spaces: ConfigurationSpaces, publications: Publications, interactions):
        self.root = Path(root)
        self.spaces = spaces
        self.publications = publications
        self.interactions = interactions

    def _resolve_space_id(self, requested: str | None) -> str:
        catalog = self.spaces.snapshot()
        if not requested:
            return catalog["active"]
        for item in catalog["spaces"]:
            if item["id"] == requested or item["name"] == requested:
                return item["id"]
        raise ConfigError(f"未找到配置空间：{requested}")

    def spaces_overview(self) -> dict:
        catalog = self.spaces.snapshot()
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
                "subscription_links": [
                    {"id": l["id"], "proxy": l["proxy"], "url": l["url"], "rule_address": l["rule_address"]}
                    for l in links
                ],
            })
        return {"active_space": catalog["active"], "spaces": rows}

    def diagnose_query(self, request: dict) -> dict:
        space_id = self._resolve_space_id(request.get("space_id"))
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
        space_id = self._resolve_space_id(request.get("space_id"))
        kind, val, normalized = _normalize_rule_entry(request.get("entry") or "")
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
            valid_targets = {"DIRECT": "DIRECT", "直连": "DIRECT", "REJECT": "REJECT", "拦截": "REJECT"}
            for name in (*groups, *nodes):
                valid_targets[name] = name
                valid_targets[name.lower()] = name
            resolved_target = valid_targets.get(raw_target) or valid_targets.get(raw_target.lower())
            if not resolved_target:
                raise ConfigError(f"未找到出口「{raw_target}」；可选出口：{', '.join(['DIRECT', 'REJECT', *groups[:15]])}")
            suffix = ",no-resolve" if kind in {"IP-CIDR", "IP-CIDR6"} else ""
            rule_line = f"{kind},{val},{resolved_target}{suffix}"
            policies = list(native.get("policies") or [])
            for item in policies:
                if item.get("text") == rule_line and item.get("enabled", True):
                    return {
                        "space_id": space_id,
                        "status": "already_exists",
                        "rule": rule_line,
                        "target": resolved_target,
                    }
            new_row = {
                "id": "hm_" + hashlib.sha256(rule_line.encode("utf-8")).hexdigest()[:18],
                "text": rule_line,
                "enabled": True,
            }
            insert_idx = len(policies)
            for idx, item in enumerate(policies):
                if str(item.get("text") or "").startswith(("MATCH,", "FINAL,")):
                    insert_idx = max(0, idx)
                    break
            policies.insert(insert_idx, new_row)
            native["policies"] = policies
            self.spaces.save_profile(profile, revision, space_id)
            return {
                "space_id": space_id,
                "status": "added",
                "mode": "native",
                "rule": rule_line,
                "target": resolved_target,
            }

        # Portable space
        target_map = {"DIRECT": "DIRECT", "直连": "DIRECT", "REJECT": "REJECT", "拦截": "REJECT"}
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

        resolved_target = target_map.get(raw_target) or target_map.get(raw_target.lower())
        if not resolved_target:
            available = ["DIRECT", "REJECT", *(g["name"] for g in profile.get("groups", []))]
            raise ConfigError(f"未找到出口「{raw_target}」；可选出口：{', '.join(available[:15])}")

        rule_name = str(request.get("rule_name") or f"Hermes · {target_labels[resolved_target]}").strip()[:80]
        library = profile.setdefault("library", [])
        lib_item = next((r for r in library if r["name"] == rule_name and not r.get("source")), None)
        if lib_item is None:
            lid = "l_hm_" + hashlib.sha256((rule_name + "\0" + resolved_target).encode("utf-8")).hexdigest()[:12]
            lib_item = {"id": lid, "name": rule_name, "kind": "classical", "entries": [normalized], "enabled": True}
            library.append(lib_item)
        else:
            if normalized not in lib_item["entries"]:
                lib_item["entries"].append(normalized)
            lib_item["enabled"] = True

        selection = profile.get("composition")
        if isinstance(selection, dict) and isinstance(selection.get("rules"), list):
            if lib_item["id"] not in selection["rules"]:
                selection["rules"].append(lib_item["id"])

        def ensure_policy(pol_list: list) -> str:
            existing = next(
                (p for p in pol_list if p.get("type") == "RULE-SET" and p.get("value") == lib_item["id"]),
                None,
            )
            if existing:
                existing["target"] = resolved_target
                existing["enabled"] = True
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

        pid = ensure_policy(profile.setdefault("policies", []))
        plans = profile.get("routing_plans") or []
        plan_id = (selection or {}).get("plan") or profile.get("active_plan")
        for plan in plans:
            if plan["id"] == plan_id:
                pid = ensure_policy(plan.setdefault("policies", []))
        if isinstance(selection, dict) and isinstance(selection.get("policies"), list):
            if pid not in selection["policies"]:
                selection["policies"].insert(0, pid)

        self.spaces.save_profile(profile, revision, space_id)
        return {
            "space_id": space_id,
            "status": "added",
            "mode": "portable",
            "rule_name": rule_name,
            "entry": normalized,
            "target": target_labels[resolved_target],
        }

    def nodes_overview(self, request: dict) -> dict:
        space_id = self._resolve_space_id(request.get("space_id"))
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
