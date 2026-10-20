"""Condition-list forms over native expressions; parsing stays on the server."""
from copy import deepcopy

from .model import ConfigError
from .native_policy import MATCHERS, _children, _validate_value, prepare_request, split_target


def condition_editor(text):
    try:
        expression, _, params = split_target(text)
    except ConfigError:
        return {"mode": "advanced", "text": text}
    nested = _children(expression)
    operator, leaves = nested or ("or", [expression])
    if params not in ([], ["no-resolve"]) or nested and params:
        return {"mode": "advanced", "text": text}
    conditions = []
    for leaf in leaves:
        values = _children(leaf)
        if values and values[0] == "or":
            atoms = [a.split(",") for a in values[1]]
            if all(len(a) == 2 and a[0] in MATCHERS and a[0] != "NETWORK" for a in atoms) and len({a[0] for a in atoms}) == 1:
                conditions.append({"type": atoms[0][0], "value": "\n".join(a[1] for a in atoms), "no_resolve": False})
                continue
        atom = leaf.split(",")
        if len(atom) not in {2, 3} or atom[0] not in {*MATCHERS, "RULE-SET"} or len(atom) == 3 and atom[2] != "no-resolve":
            return {"mode": "advanced", "text": text}
        # Parentheses belong to a nested expression, which this flat form cannot edit.
        if not atom[1] or any(char in atom[1] for char in "()\r\n"):
            return {"mode": "advanced", "text": text}
        conditions.append({"type": atom[0], "value": atom[1], "no_resolve": len(atom) == 3 or params == ["no-resolve"]})
    if operator == "or" and len(conditions) > 1 and len({(c["type"], c["no_resolve"]) for c in conditions}) == 1 and conditions[0]["type"] not in {"RULE-SET", "NETWORK"}:
        conditions = [{**conditions[0], "value": "\n".join(c["value"] for c in conditions)}]
    return {"mode": "conditions", "operator": operator, "conditions": conditions}


def validate_conditions(draft):
    conditions = draft.get("conditions")
    if not isinstance(draft.get("operator", "or"), str) or draft.get("operator", "or") not in {"or", "and"}:
        raise ConfigError("请选择条件之间的 AND / OR 匹配关系")
    if not isinstance(conditions, list) or not 1 <= len(conditions) <= 128:
        raise ConfigError("请至少添加一个匹配条件，最多 128 个")
    result = []
    for index, item in enumerate(conditions, 1):
        if not isinstance(item, dict) or not isinstance(item.get("type"), str) or item.get("type") not in {*MATCHERS, "RULE-SET"}:
            raise ConfigError(f"条件 {index} 的匹配类型无效")
        condition = {"type": item["type"], "value": item.get("value", ""), "no_resolve": item.get("no_resolve", False)}
        if not isinstance(condition["value"], str):
            raise ConfigError(f"条件 {index} 的匹配内容需要文本")
        if type(condition["no_resolve"]) is not bool:
            raise ConfigError(f"条件 {index} 的解析选项无效")
        source = item.get("source")
        if source is not None:
            if item["type"] != "RULE-SET" or not isinstance(source, dict) or condition["value"]:
                raise ConfigError(f"条件 {index} 的订阅来源无效")
            if any(not isinstance(source.get(key, ""), str) for key in ("name", "url", "behavior", "source_format")):
                raise ConfigError(f"条件 {index} 的订阅字段需要文本")
            condition["source"] = deepcopy(source)
        else:
            values = [v.strip() for v in condition["value"].splitlines() if v.strip()]
            if not values or len(values) > 128 or len(values) != len(set(values)):
                raise ConfigError(f"条件 {index} 需为 1–128 条不重复内容，每行一条")
            if condition["type"] in {"RULE-SET", "NETWORK"} and len(values) != 1:
                raise ConfigError(f"条件 {index} 的订阅或协议只能选择一项")
            for line, value in enumerate(values, 1):
                try:
                    _validate_value(condition["type"], value)
                    if condition["type"] in {"DOMAIN", "DOMAIN-SUFFIX"}:
                        from .model import check_domain
                        check_domain(value, "域名")
                except ConfigError as error:
                    raise ConfigError(f"条件 {index} 第 {line} 行：{error}") from None
            condition["value"] = "\n".join(values)
            if any(char in condition["value"] for char in "()\r"):
                raise ConfigError(f"条件 {index} 不能包含括号或换行；复杂表达式请使用完整原文")
        result.append(condition)
    return result


def materialize_conditions(native, draft, conditions, unit, fetch=False):
    """Reuse source registration and emit the same native rule input as older forms."""
    sources = [item["source"] for item in conditions if "source" in item]
    if len(sources) > 16:
        raise ConfigError("一次最多添加 16 个订阅")
    if sources:
        native, added = prepare_request(native, {"draft": {"mode": "rules", "name": draft.get("name", ""), "rules": [], "target": draft.get("target")}, "sources": sources}, fetch=fetch)
        from .native_policy import decode
        names = iter(decode(added["text"])["rules"])
        for item in conditions:
            if "source" in item:
                item["value"] = next(names)
                del item["source"]
    seen = set()
    for item in conditions:
        if item["type"] == "RULE-SET" and item["value"] not in native["mihomo"].get("rule-providers", {}):
            raise ConfigError("条件引用的规则来源已不存在，请移除后重新添加")
        key = (item["type"], item["value"], item["no_resolve"])
        if key in seen:
            raise ConfigError("匹配条件不能重复")
        seen.add(key)
    operator = draft.get("operator", "or")
    def atom(item):
        values = item["value"].splitlines()
        leaves = [item["type"] + "," + value + (",no-resolve" if item["no_resolve"] else "") for value in values]
        return leaves[0] if len(leaves) == 1 else "OR,(" + ",".join("(" + v + ")" for v in leaves) + ")"
    atoms = [atom(item) for item in conditions]
    expression = atoms[0] if len(atoms) == 1 else operator.upper() + ",(" + ",".join("(" + atom + ")" for atom in atoms) + ")"
    text = expression + "," + str(draft.get("target"))
    # Single-leaf no-resolve is an action suffix in native syntax.
    if len(atoms) == 1 and conditions[0]["no_resolve"] and "\n" not in conditions[0]["value"]:
        text = conditions[0]["type"] + "," + conditions[0]["value"] + "," + str(draft.get("target")) + ",no-resolve"
    if unit and len(unit["parts"]) == 1:
        before = unit.get("condition_editor", {})
        if before.get("conditions") == conditions and (len(conditions) == 1 or before.get("operator") == operator):
            original = next(row for row in native["policies"] if row["id"] == unit["id"])
            old_expression, _, params = split_target(original["text"])
            text = ",".join([old_expression, str(draft.get("target")), *params])
    return native, {**draft, "mode": "advanced", "text": text, "set_target": True}, conditions
