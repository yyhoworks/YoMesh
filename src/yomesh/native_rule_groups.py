"""One editable rule group joins matching and its application outlet.

This is an editor view over authoritative native policies and groups. Reading
or reordering never collapses native expressions, options or group fields.
"""
from copy import deepcopy
import re
import uuid
from urllib.parse import urlparse

import yaml

from .model import ConfigError

BUILTINS = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL"}
MODES = {"select", "url-test", "fallback", "load-balance"}


def source_settings(native, refs):
    """Editable fields for the sources already owned by this editor unit."""
    providers = native["mihomo"].get("rule-providers", {})
    result = []
    for ref in refs:
        provider = providers.get(ref)
        if provider is None:
            result.append({"ref": ref, "kind": "missing"})
            continue
        kind = "subscription" if provider.get("type") == "http" else "local"
        path = str(provider.get("path", "")).removeprefix("./")
        body = yaml.safe_dump({"payload": provider["payload"]}, allow_unicode=True, sort_keys=False) if provider.get("type") == "inline" and "payload" in provider else native.get("documents", {}).get(path)
        result.append({"ref": ref, "kind": kind, "url": provider.get("url", ""), "path": path, "body": body if kind == "local" else None, "editable_text": provider.get("format", "yaml") == "yaml" and isinstance(body, str), "source_format": "clash-rules" if ref in native.get("rule_source_adapters", {}) else "payload"})
    return result


def organization(native):
    groups = {g["name"]: g for g in native["mihomo"].get("proxy-groups", [])}
    incoming = {member for group in groups.values() for member in group.get("proxies", []) if member in groups}
    explicit = native.get("group_roles", {})
    roles = {}
    for name, group in groups.items():
        owns_nodes = bool(group.get("use") or any(group.get(k) for k in ("include-all", "include-all-providers", "include-all-proxies")) or any(m not in groups and m not in BUILTINS for m in group.get("proxies", [])))
        roles[name] = explicit.get(name, "nodes" if owns_nodes else "shared" if name in incoming else "rules")
    return groups, roles


def rule_groups(native):
    from .native_policy import row_view, targets
    groups, roles = organization(native)
    target_rows = targets(native)
    result = []
    for policy in native["policies"]:
        view = row_view(native, policy, target_rows)
        outlet = groups.get(view["target"]) if roles.get(view["target"]) == "rules" else None
        simple = policy["text"].startswith("RULE-SET,") and view["editor"]["mode"] == "rules" and len(view["editor"]["rules"]) == 1
        # Only adjacent simple sources with the same owned outlet are grouped.
        # Never move a source across another policy or fold an AND expression.
        if result and outlet and simple and result[-1].get("_simple") and result[-1]["target"] == view["target"] and view["editor"]["rules"][0] not in result[-1]["editor"]["rules"] and not result[-1]["fallback"]:
            unit = result[-1]
            unit["policy_ids"].append(policy["id"])
            unit["parts"].append(view)
            unit["editor"]["rules"] = list(dict.fromkeys([*unit["editor"]["rules"], *view["editor"]["rules"]]))
            unit["summary"] = "规则 · " + "、".join(unit["editor"]["rules"])
            unit["unavailable_rules"] = list(dict.fromkeys([*unit["unavailable_rules"], *view["unavailable_rules"]]))
            unit["sources"].extend(view["sources"])
            unit["mixed_enabled"] = len({p["enabled"] for p in unit["parts"]}) > 1
            unit["enabled"] = any(p["enabled"] for p in unit["parts"])
            continue
        unit = {**deepcopy(view), "policy_ids": [policy["id"]], "parts": [view], "mixed_enabled": False, "_simple": simple}
        unit["outlet"] = None
        if outlet:
            unit["name"] = policy.get("name") or outlet["name"]
            unit["outlet"] = {"name": outlet["name"], "type": outlet.get("type", "select"), "members": list(outlet.get("proxies", [])), "default": next(iter(outlet.get("proxies", [])), ""), "providers": list(outlet.get("use", [])), "interval": outlet.get("interval", 300), "tolerance": outlet.get("tolerance", 50), "test_url": outlet.get("url", "https://www.gstatic.com/generate_204")}
        result.append(unit)
    from .native_rule_conditions import condition_editor
    originals = {p["id"]: p["text"] for p in native["policies"]}
    for unit in result:
        unit.pop("_simple", None)
        if len(unit["parts"]) == 1:
            unit["condition_editor"] = condition_editor(originals[unit["id"]])
        else:
            unit["condition_editor"] = {"mode": "conditions", "operator": "or", "conditions": [condition_editor(originals[part["id"]])["conditions"][0] for part in unit["parts"]]}
        refs = list(dict.fromkeys(ref for part in unit["parts"] for ref in re.findall(r"RULE-SET,([^,)]+)", originals[part["id"]])))
        unit["source_settings"] = source_settings(native, refs)
        if unit["outlet"]:
            unit["outlet"]["shared_units"] = sum(1 for other in result if other["target"] == unit["target"])
            outlet = unit["outlet"]
            label = {"select": "手动选择", "url-test": "自动测速", "fallback": "故障转移", "load-balance": "负载均衡"}.get(outlet["type"], outlet["type"])
            unit["outlet_summary"] = label + " · " + str(len(outlet["members"])) + " 个候选" + (" · 默认 " + outlet["default"] if outlet["type"] == "select" and outlet["default"] else "")
        else:
            unit["outlet_summary"] = next((t["label"] for t in target_rows if t["value"] == unit["target"]), unit["target"])
    return result


def overview(native):
    from .native_policy import targets
    groups, roles = organization(native)
    units = rule_groups(native)
    used = {u["target"] for u in units}
    choices = targets(native)
    manual = {p["name"] for p in native["mihomo"].get("proxies", [])}
    def descendants(name, trail=()):
        if name in trail:
            return set()
        children = {m for m in groups[name].get("proxies", []) if m in groups}
        return children | set().union(*(descendants(child, (*trail, name)) for child in children)) if children else set()
    # Editor locations are derived from the same rule units as the main list.
    # A selector used by an application is not another region/node group.
    management = []
    for name in groups:
        owners = [{"id": u["id"], "name": u["name"]} for u in units if u["target"] == name]
        editor = {"type": "rule", "id": owners[0]["id"]} if roles[name] == "rules" and owners else {"type": "group", "name": name, "role": roles[name]}
        label = {"nodes": "线路节点组", "rules": "规则组出口", "shared": "共用出口"}[roles[name]] + " · " + name
        subscription = bool(groups[name].get("use") or groups[name].get("include-all") or groups[name].get("include-all-providers"))
        section = ("subscription" if subscription else "manual") if roles[name] == "nodes" else roles[name]
        category = ("lines" if subscription else "home") if roles[name] == "nodes" else "shared" if roles[name] == "shared" or not owners else None
        if category:
            label = {"lines": "线路组", "home": "回家组", "shared": "共用出口组"}[category] + " · " + name
        # An orphaned application selector remains editable as an unbound outlet.
        # Display classification never changes its native role or configuration.
        users = [{"id": u["id"], "name": u["name"]} for u in units if u["target"] == name or u["target"] in groups and name in descendants(u["target"])]
        management.append({"name": name, "label": label, "role": roles[name], "section": section, "category": category, "owners": owners, "used_by_rules": users, "editor": editor})
    locations = {m["name"]: m for m in management}
    members = [{**t, **({"label": locations[t["value"]]["label"], "group_role": roles[t["value"]], "editor": locations[t["value"]]["editor"]} if t["value"] in groups else {}), "resource": "group" if t["value"] in groups else "node" if t["value"] in manual else "builtin", "blocked_for": sorted(descendants(t["value"])) if t["value"] in groups else []} for t in choices]
    return {"rule_groups": units, "node_groups": [m["name"] for m in management if m["category"]], "shared_outlets": [name for name in groups if roles[name] == "shared"], "outlet_choices": [t for t in members if t["value"] not in groups or roles[t["value"]] != "rules"], "group_member_choices": members, "group_management": management, "unbound_outlets": [name for name in groups if roles[name] == "rules" and name not in used]}


def _unit(native, ident):
    unit = next((u for u in rule_groups(native) if u["id"] == ident), None)
    if ident and unit is None:
        raise ConfigError("规则组已变化，请刷新后重试")
    return unit


def _replace_target(row, target):
    from .native_policy import split_target
    expression, previous, params = split_target(row["text"])
    if previous != target:
        row["text"] = ",".join([expression, target, *params])


def _edit_sources(native, request, draft, unit, fetch):
    """Edit attached source fields atomically; shared sources get private copies."""
    from .native_configuration import refresh_provider, validate_rule_yaml
    edits = request.get("source_edits", [])
    if not isinstance(edits, list) or len(edits) > 128:
        raise ConfigError("规则来源表单无效")
    allowed = {s["ref"]: s for s in (unit or {}).get("source_settings", [])}
    seen, origins = set(), {}
    providers = native["mihomo"].setdefault("rule-providers", {})
    unit_ids = set((unit or {}).get("policy_ids", []))
    for edit in edits:
        if not isinstance(edit, dict) or not isinstance(edit.get("ref"), str):
            raise ConfigError("规则来源表单无效")
        ref = edit["ref"]
        if ref not in allowed or ref in seen or ref not in draft.get("rules", []):
            raise ConfigError("只能编辑当前规则组里的来源，请刷新后重试")
        seen.add(ref)
        old = allowed[ref]
        if old["kind"] == "subscription":
            field, value = "url", edit.get("url")
            if not isinstance(value, str) or len(value) > 8192 or urlparse(value).scheme not in {"http", "https"} or not urlparse(value).hostname:
                raise ConfigError("请填写完整的 HTTP / HTTPS 订阅地址")
            if value == old["url"]:
                continue
        elif old["kind"] == "local" and old["editable_text"]:
            field, value = "body", edit.get("body")
            if not isinstance(value, str) or len(value.encode()) > 8 * 1024 * 1024:
                raise ConfigError("本地规则内容需要文本，最多 8 MiB")
            if value == old["body"]:
                continue
            validate_rule_yaml(value)
        else:
            raise ConfigError("此来源请通过原生文件入口编辑")
        pattern = re.compile(r"RULE-SET," + re.escape(ref) + r"(?=[,)])")
        shared = any(p["id"] not in unit_ids and pattern.search(p["text"]) for p in native["policies"]) or any(pattern.search(text) for text in native.get("documents", {}).values()) or any(pattern.search(str(p.get("payload", []))) for p in providers.values())
        name = ref
        if shared:
            name = ref[:60] + "-" + uuid.uuid4().hex[:8]
            provider = deepcopy(providers[ref])
            path = str(provider.get("path", "")).removeprefix("./")
            if path:
                suffix = "." + path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ".yaml"
                new_path = "ruleset/user-" + uuid.uuid4().hex + suffix
                provider["path"] = new_path
                for key in ("documents", "assets"):
                    if path in native.get(key, {}):
                        native[key][new_path] = native[key][path]
            providers[name] = provider
            from .native_rule_sources import copy_adapter
            copy_adapter(native, ref, name)
            draft["rules"] = [name if r == ref else r for r in draft["rules"]]
            selection = native.get("selection", {}).get("rules")
            if selection is not None and ref in selection:
                selection.append(name)
            origins[name] = ref
        if field == "url":
            providers[name]["url"] = value
            if fetch:
                refreshed = refresh_provider(native, "rule-providers", name)
                native.clear()
                native.update(refreshed)
                providers = native["mihomo"]["rule-providers"]
        elif providers[name].get("type") == "inline":
            from .native_configuration import rule_yaml_entries
            providers[name]["payload"] = rule_yaml_entries(value)
        else:
            native.setdefault("documents", {})[str(providers[name]["path"]).removeprefix("./")] = value
    return origins


def _apply_outlet(native, request, draft, unit):
    outlet = request.get("outlet")
    if outlet is None:
        return
    if not isinstance(outlet, dict) or outlet.get("type") not in MODES:
        if isinstance(outlet, dict) and outlet.get("type") == "direct":
            return
        raise ConfigError("请选择固定出口、手动选择、自动测速、故障转移或负载均衡")
    groups = native["mihomo"].setdefault("proxy-groups", [])
    old_name = unit["outlet"]["name"] if unit and unit["outlet"] else None
    original = next((g for g in groups if g["name"] == old_name), None)
    members = outlet.get("members")
    if not isinstance(members, list) or len(members) > 512 or any(not isinstance(m, str) for m in members) or len(set(members)) != len(members):
        raise ConfigError("出口节点组需要不重复的成员列表，最多 512 项")
    if not members and not (original and original.get("use")):
        raise ConfigError("请至少选择一个出口节点组")
    from .native_configuration import cached_proxy_names
    names = {g["name"] for g in groups} | {p["name"] for p in native["mihomo"].get("proxies", [])} | cached_proxy_names(native) | BUILTINS
    if any(m not in names or m == old_name for m in members):
        raise ConfigError("出口包含不存在的成员，或引用了自身")
    if original:
        group = deepcopy(original)
    else:
        name = str(draft.get("name", "")).strip() or "规则出口"
        name = name[:60]
        existing = {g["name"] for g in groups}
        if name in existing:
            name += " · " + uuid.uuid4().hex[:8]
        group = {"name": name}
    group.update(type=outlet["type"], proxies=list(members))
    selected = outlet.get("default")
    if group["type"] == "select" and selected:
        if selected not in members:
            raise ConfigError("默认出口需在已选成员中")
        group["proxies"] = [selected, *[m for m in members if m != selected]]
    if group["type"] != "select":
        for key, default, minimum in (("interval", 300, 1), ("tolerance", 50, 0)):
            value = outlet.get(key, group.get(key, default))
            if type(value) is not int or not minimum <= value <= 86400:
                raise ConfigError("测速间隔或容差无效")
            if key != "tolerance" or group["type"] == "url-test":
                group[key] = value
        url = outlet.get("test_url", group.get("url", "https://www.gstatic.com/generate_204"))
        from urllib.parse import urlparse
        if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).hostname:
            raise ConfigError("测速地址需要完整的 HTTP / HTTPS URL")
        group["url"] = url
    if original:
        groups[groups.index(original)] = group
    else:
        groups.append(group)
        native.setdefault("group_roles", {})[group["name"]] = "rules"
    lookup = {g["name"]: g for g in groups}
    def cycle(name, trail=()):
        if name in trail:
            raise ConfigError("出口节点组存在循环引用")
        for child in lookup.get(name, {}).get("proxies", []):
            if child in lookup:
                cycle(child, (*trail, name))
    cycle(group["name"])
    if native.get("selection", {}).get("groups") is not None and group["name"] not in native["selection"]["groups"]:
        native["selection"]["groups"].append(group["name"])
    draft["target"] = group["name"]


def prepare_group(native, request, fetch=False):
    from .native_policy import prepare_request, decode
    updated = deepcopy(native)
    unit = _unit(native, request.get("id"))
    draft = deepcopy(request.get("draft"))
    if not isinstance(draft, dict):
        raise ConfigError("规则组表单无效")
    structured = draft.get("mode") == "conditions"
    conditions = None
    if structured:
        from .native_rule_conditions import validate_conditions, materialize_conditions
        if request.get("sources"):
            raise ConfigError("请在对应条件中填写订阅地址")
        conditions = validate_conditions(draft)
        draft["rules"] = [item["value"] for item in conditions if item["type"] == "RULE-SET" and "source" not in item]
    origins = _edit_sources(updated, request, draft, unit, fetch)
    _apply_outlet(updated, request, draft, unit)
    if structured:
        renamed = {old: new for new, old in origins.items()}
        for item in conditions:
            if item["type"] == "RULE-SET":
                item["value"] = renamed.get(item["value"], item["value"])
        updated, draft, conditions = materialize_conditions(updated, draft, conditions, unit, fetch)
        separate = draft.get("operator", "or") == "or" and all(item["type"] == "RULE-SET" for item in conditions)
        if unit and unit["mixed_enabled"] and not separate:
            raise ConfigError("此组部分来源已停用；请先在规则列表统一启停，再更改条件组合")
    updated, prepared = prepare_request(updated, {**request, "draft": draft, **({"sources": []} if structured else {})}, fetch=fetch)
    rows = [prepared]
    original_rows = [p for p in native["policies"] if unit and p["id"] in unit["policy_ids"]]
    if structured and len(original_rows) > 1 and separate:
        old_by_ref = {decode(p["text"])["rules"][0]: p for p in original_rows}
        rows = []
        for item in conditions:
            ref = item["value"]
            old = old_by_ref.get(origins.get(ref, ref))
            row = deepcopy(old) if old else {"id": "np_" + uuid.uuid4().hex, "enabled": draft.get("enabled", True)}
            row["text"] = "RULE-SET," + ref + "," + draft["target"] + (",no-resolve" if item["no_resolve"] else "")
            rows.append(row)
        if rows and original_rows[0]["id"] not in {r["id"] for r in rows}:
            rows[0]["id"] = original_rows[0]["id"]
    elif len(original_rows) > 1 and draft.get("mode") == "rules" and draft.get("operator", "or") == "or":
        # Keep domain/IP sources as separate native lines and retain each suffix.
        desired = decode(prepared["text"])["rules"]
        old_by_ref = {decode(p["text"])["rules"][0]: p for p in original_rows}
        rows = []
        for ref in desired:
            old = old_by_ref.get(origins.get(ref, ref))
            row = deepcopy(old) if old else {"id": "np_" + uuid.uuid4().hex, "text": "RULE-SET," + ref + "," + draft["target"] + (",no-resolve" if draft.get("no_resolve") else ""), "enabled": draft.get("enabled", True)}
            if ref in origins:
                row["text"] = "RULE-SET," + ref + row["text"][len("RULE-SET," + origins[ref]):]
            _replace_target(row, draft["target"])
            if request.get("enabled_dirty") is True:
                row["enabled"] = draft.get("enabled", True)
            rows.append(row)
        # Retain the stable group ID even if the first source was removed.
        if rows and original_rows[0]["id"] not in {r["id"] for r in rows}:
            rows[0]["id"] = original_rows[0]["id"]
    if unit:
        if draft.get("name", "").strip() == unit["name"]:
            if "name" in original_rows[0]:
                rows[0]["name"] = original_rows[0]["name"]
            else:
                rows[0].pop("name", None)
        elif draft.get("name", "").strip():
            rows[0]["name"] = draft["name"].strip()
        if request.get("enabled_dirty") is not True and len(original_rows) == 1:
            rows[0]["enabled"] = original_rows[0]["enabled"]
        original_ids = set(unit["policy_ids"])
        insertion = next(i for i, p in enumerate(updated["policies"]) if p["id"] == unit["id"])
        updated["policies"] = [p for p in updated["policies"] if p["id"] not in original_ids]
    else:
        insertion = len(updated["policies"]) - 1
    updated["policies"][insertion:insertion] = rows
    return updated, rows


def preview(native, request):
    updated, rows = prepare_group(native, request, fetch=False)
    unit = next(u for u in rule_groups(updated) if u["id"] == rows[0]["id"])
    count = sum(len(c.get("value", "").splitlines()) for c in (unit.get("condition_editor", {}).get("conditions") or []))
    editor = unit.get("condition_editor", {})
    if editor.get("mode") == "conditions":
        from .native_policy import MATCHERS
        pieces = []
        for condition in editor["conditions"]:
            values = condition["value"].splitlines()
            label = "规则订阅" if condition["type"] == "RULE-SET" else MATCHERS.get(condition["type"], condition["type"])
            pieces.append(label + ("（任意 " + str(len(values)) + " 条）" if len(values) > 1 else "：" + values[0]))
        effect = ("，并且" if editor.get("operator") == "and" else "，或者").join(pieces) + " → " + unit["outlet_summary"]
    else:
        effect = unit["summary"] + " → " + unit["outlet_summary"]
    return {"policy": unit, "text": "\n".join(p["text"] for p in rows), "effect": effect, "condition_count": count}


def change(native, request):
    from .native_policy import _validate_target
    action, ident = request.get("action"), request.get("id")
    if action == "unit-save":
        return prepare_group(native, request, fetch=True)[0]
    updated = deepcopy(native)
    units = rule_groups(native)
    if action == "unit-reorder":
        ids = request.get("ids")
        if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids) or set(ids) != {u["id"] for u in units} or ids[-1] != units[-1]["id"]:
            raise ConfigError("规则组列表已变化，或兜底未放在最后")
        lookup = {u["id"]: u for u in units}
        policies = {p["id"]: p for p in updated["policies"]}
        updated["policies"] = [policies[p] for ident in ids for p in lookup[ident]["policy_ids"]]
        return updated
    unit = _unit(native, ident)
    if not unit:
        raise ConfigError("规则组不存在")
    ids = set(unit["policy_ids"])
    rows = [p for p in updated["policies"] if p["id"] in ids]
    if action == "unit-target":
        target = request.get("target")
        for row in rows:
            _validate_target(updated, target, row)
            _replace_target(row, target)
    elif not unit["fallback"]:
        if action == "unit-toggle" and type(request.get("enabled")) is bool:
            for row in rows:
                row["enabled"] = request["enabled"]
        elif action == "unit-delete":
            updated["policies"] = [p for p in updated["policies"] if p["id"] not in ids]
        elif action == "unit-copy":
            copied = deepcopy(rows)
            for row in copied:
                row["id"] = "np_" + uuid.uuid4().hex
            copied[0]["name"] = unit["name"][:76] + " 副本"
            if unit["outlet"]:
                groups = updated["mihomo"]["proxy-groups"]
                original = next(g for g in groups if g["name"] == unit["outlet"]["name"])
                name = original["name"][:60] + " 副本 " + uuid.uuid4().hex[:8]
                groups.append({**deepcopy(original), "name": name})
                updated.setdefault("group_roles", {})[name] = "rules"
                if updated.get("selection", {}).get("groups") is not None:
                    updated["selection"]["groups"].append(name)
                for row in copied:
                    _replace_target(row, name)
            index = next(i for i, p in enumerate(updated["policies"]) if p["id"] == rows[-1]["id"])
            updated["policies"][index + 1:index + 1] = copied
        else:
            raise ConfigError("规则组操作无效")
    else:
        raise ConfigError("兜底需保持启用并固定最后")
    return updated
