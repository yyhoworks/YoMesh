"""Display names for group categories; never part of a kernel Profile."""
from .model import ConfigError

DEFAULT_NAMES = {"lines": "线路组", "home": "回家组", "shared": "共用出口组"}


def category_names(value=None):
    if value is None:
        return dict(DEFAULT_NAMES)
    if not isinstance(value, dict) or set(value) != set(DEFAULT_NAMES):
        raise ConfigError("请填写线路、回家与共用出口三个分类名称")
    result = {}
    for key, name in value.items():
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 12 or not name.isprintable():
            raise ConfigError("分类名称需要 1–12 个可显示字符")
        result[key] = name.strip()
    if len({name.casefold() for name in result.values()}) != len(DEFAULT_NAMES):
        raise ConfigError("三个分类名称不能相同")
    return result


def decorate_categories(view, names):
    locations = {row["name"]: row for row in view.get("group_management", [])}
    for row in locations.values():
        if row.get("category") in names:
            row["label"] = names[row["category"]] + " · " + row["name"]
    for key in ("group_member_choices", "outlet_choices", "targets"):
        for choice in view.get(key, []):
            location = locations.get(choice["value"])
            if location:
                choice["category"] = location.get("category")
                choice["label"] = location["label"]
