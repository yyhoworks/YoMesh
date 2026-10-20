"""Resolve resource selections into a complete Profile before native compilation."""
from copy import deepcopy

from .model import ConfigError, Profile
from .rulelogic import references


def validate_selection(selection, value):
    if not isinstance(selection, dict) or set(selection) - {"groups", "rules", "policies", "plan", "fallback"}:
        raise ConfigError("生成方案字段无效")
    tables = {"groups": value.get("groups", []), "rules": value.get("library", []), "policies": value.get("policies", [])}
    if selection.get("plan"):
        plan = next((p for p in value.get("routing_plans", []) if p["id"] == selection["plan"]), None)
        if not plan:
            raise ConfigError("选择的分流方案不存在")
        tables["policies"] = plan["policies"]
    for key, entries in tables.items():
        chosen = selection.get(key)
        if chosen is not None and (not isinstance(chosen, list) or not all(isinstance(i, str) for i in chosen) or len(set(chosen)) != len(chosen) or set(chosen) - {i["id"] for i in entries}):
            raise ConfigError(key + ": 选择包含重复或不存在的资源，请重新选择")
    if selection.get("fallback") is not None and selection["fallback"] not in {"DIRECT", *(g["id"] for g in value.get("groups", [])), *(n["id"] for n in value.get("nodes", []))}:
        raise ConfigError("生成方案的兜底出口不存在")
    return deepcopy(selection)


def resolve_composition(value, include_proxy=True):
    if not isinstance(value, dict):
        return value, None
    selection = value.get("composition")
    disabled = {r["id"] for r in value.get("library", []) if r.get("enabled") is False}
    if not selection and not disabled:
        return value, None
    selection = validate_selection(selection or {}, value)
    result = deepcopy(value)
    result.pop("composition", None)
    selected_rules = set(selection.get("rules") if selection.get("rules") is not None else (r["id"] for r in result.get("library", []))) - disabled
    excluded = []
    def policies(items, active=True):
        chosen = selection.get("policies") if active else None
        rows = []
        for item in items:
            if not item.get("enabled", True):
                continue
            if chosen is not None and item["id"] not in chosen:
                continue
            missing = references(item) - selected_rules
            if missing:
                excluded.append({"id": item["id"], "name": item["name"], "rules": sorted(missing), "reason": "引用了此配置空间中未启用的规则，整条策略不参与生成"})
                continue
            rows.append(item)
        return rows
    if selection.get("plan"):
        plan = next(p for p in result.get("routing_plans", []) if p["id"] == selection["plan"])
        result.update(active_plan=plan["id"], policies=deepcopy(plan["policies"]), fallback=plan["fallback"])
    result["policies"] = policies(result.get("policies", []))
    if selection.get("fallback") is not None:
        result["fallback"] = selection["fallback"]
    active = result.get("active_plan")
    result["inbounds"] = [i for i in result.get("inbounds", []) if i.get("enabled", True)]
    needed_plans = {active, *(i["plan"] for i in result["inbounds"])}
    result["routing_plans"] = [p for p in result.get("routing_plans", []) if p["id"] in needed_plans]
    for plan in result["routing_plans"]:
        if plan["id"] == active:
            plan.update(policies=deepcopy(result["policies"]), fallback=result["fallback"])
        else:
            plan["policies"] = policies(plan["policies"], False)
    result["library"] = [r for r in result.get("library", []) if r["id"] in selected_rules]
    for rule in result["library"]:
        rule.pop("enabled", None)
    if selection.get("groups") is not None and include_proxy:
        groups = {g["id"]: g for g in result.get("groups", [])}
        chosen = set(selection["groups"])
        def children(ident, trail=()):
            if ident in trail:
                raise ConfigError("节点组存在循环引用")
            for child in groups[ident].get("groups", []):
                chosen.add(child)
                children(child, (*trail, ident))
        for ident in list(chosen):
            children(ident)
        result["groups"] = [g for g in result.get("groups", []) if g["id"] in chosen]
        # A selected policy must never silently gain a different outbound.
        all_policies = [*result["policies"], *(p for plan in result["routing_plans"] for p in plan["policies"])]
        targets = {result.get("fallback"), *(p.get("fallback") for p in result["routing_plans"]), *(p["target"] for p in all_policies)}
        missing = (targets & set(groups)) - chosen
        if missing:
            raise ConfigError("分流出口使用了未选中的节点组：" + "、".join(groups[i]["name"] for i in sorted(missing)))
        temporary = Profile({**result, "policies": [], "routing_plans": [], "inbounds": [], "fallback": "DIRECT"})
        nodes = targets & set(temporary.index["nodes"])
        for ident in chosen:
            nodes.update(i for i in temporary.members(ident) if i in temporary.index["nodes"])
        result["nodes"] = [n for n in result.get("nodes", []) if n["id"] in nodes]
        # Remove now-unselected explicit node references from otherwise valid groups.
        for group in result["groups"]:
            group["nodes"] = [i for i in group["nodes"] if i in nodes]
            if "order" in group:
                group["order"] = [i for i in group["order"] if i in nodes or i in chosen]
    return result, {"selection": selection, "excluded_policies": excluded, "groups": [g["id"] for g in result.get("groups", [])], "rules": sorted(selected_rules), "policies": [p["id"] for p in result["policies"]]}


def preview_composition(value):
    """Explain selection drafts using the same resolver used by native export.

    Recommendations are explicit suggestions, never applied while previewing.
    Rule impacts include enabled inbound plans and retain whole logical policies.
    """
    selection = validate_selection(value.get("composition") or {}, value)
    context = deepcopy(value)
    if selection.get("plan"):
        plan = next(p for p in context.get("routing_plans", []) if p["id"] == selection["plan"])
        context.update(active_plan=plan["id"], policies=deepcopy(plan["policies"]), fallback=plan["fallback"])
    if "fallback" in selection:
        context["fallback"] = selection["fallback"]
    usage = Profile.routing_usage(context)
    chosen_policies = selection.get("policies")
    rule_impacts = {}
    for ident, item in usage["library"].items():
        rule_impacts[ident] = [r for r in item["references"] if r["runtime"] and
                              (not r["current"] or chosen_policies is None or r["policy"] in chosen_policies)]

    # Resolve with all rules and groups only to discover the chosen policies'
    # requirements, including inbound-bound plans. No conditions are rewritten.
    dependency_value = deepcopy(context)
    dependency_value["composition"] = {**selection, "groups": None, "rules": None}
    for rule in dependency_value.get("library", []):
        rule.pop("enabled", None)
    dependencies, _ = resolve_composition(dependency_value)
    all_policies = [*dependencies.get("policies", []),
                    *(p for plan in dependencies.get("routing_plans", []) for p in plan["policies"])]
    all_policies = [p for p in all_policies if p.get("enabled", True)]
    required_rules = set().union(*(references(p) for p in all_policies)) if all_policies else set()
    targets = {dependencies.get("fallback"), *(p.get("fallback") for p in dependencies.get("routing_plans", [])),
               *(p["target"] for p in all_policies)}
    groups = {g["id"]: g for g in value.get("groups", [])}
    required_groups = targets & groups.keys()

    def children(ident, trail=()):
        if ident in trail:
            raise ConfigError("节点组存在循环引用")
        for child in groups[ident].get("groups", []):
            required_groups.add(child)
            children(child, (*trail, ident))

    for ident in list(required_groups):
        children(ident)
    recommended = deepcopy(selection)
    for key, table, needed in (("rules", value.get("library", []), required_rules),
                                ("groups", value.get("groups", []), required_groups)):
        if selection.get(key) is not None:
            selected = set(selection[key]) | needed
            recommended[key] = [row["id"] for row in table if row["id"] in selected]
    result = {"selection": selection, "recommended_selection": recommended,
              "rule_impacts": rule_impacts, "excluded_policies": [], "groups": [],
              "counts": {}, "valid": False, "error": ""}
    # Explanations must still be available when a selected outbound is missing.
    broad = deepcopy(value)
    broad["composition"] = {**selection, "groups": None}
    _, explanation = resolve_composition(broad)
    if explanation:
        result["excluded_policies"] = explanation["excluded_policies"]
    try:
        resolved, _ = resolve_composition(value)
        Profile(resolved)
        result["groups"] = [{k: g[k] for k in ("id", "name", "mode")} for g in resolved.get("groups", []) if g.get("enabled", True)]
        result["counts"] = {key: len([r for r in resolved.get(key, []) if r.get("enabled", True)])
                            for key in ("nodes", "groups", "library", "policies", "inbounds")}
        result["valid"] = True
    except ConfigError as error:
        result["error"] = str(error)
    return result
