"""Explicit native provider bindings stored in the configuration-space catalog."""
from copy import deepcopy
import uuid

from .model import ConfigError, Profile


SECTIONS = {"proxy-providers", "rule-providers"}


def resource(native, section, name):
    provider = native["mihomo"].get(section, {}).get(name)
    if provider is None:
        return None
    path = str(provider.get("path", "")).removeprefix("./")
    return {"provider": deepcopy(provider), "documents": {path: native["documents"][path]} if path in native.get("documents", {}) else {},
            "assets": {path: native["assets"][path]} if path in native.get("assets", {}) else {},
            "adapter": deepcopy(native.get("rule_source_adapters", {}).get(name))}


def install(native, section, name, blob):
    native["mihomo"].setdefault(section, {})[name] = deepcopy(blob["provider"])
    native.setdefault("documents", {}).update(deepcopy(blob["documents"]))
    native.setdefault("assets", {}).update(deepcopy(blob["assets"]))
    if section == "rule-providers":
        if blob.get("adapter"):
            native.setdefault("rule_source_adapters", {})[name] = deepcopy(blob["adapter"])
        else:
            native.get("rule_source_adapters", {}).pop(name, None)


def overlay(catalog, profile, scope):
    native = profile.get("native_configuration")
    if not native:
        return
    for item in catalog.state.get("native_resources", []):
        for binding in item["bindings"]:
            if binding["space"] == scope:
                install(native, item["section"], binding["name"], item["value"])


def listing(catalog, request):
    url = request.get("url", "").strip()
    section = request.get("section", "rule-providers")
    if section not in SECTIONS or not url:
        raise ConfigError("请选择资源类型并填写完整地址")
    result = []
    with catalog.lock:
        for space in catalog.state["spaces"]:
            snapshot = catalog.workspace_snapshot(space["id"])
            native = snapshot["profile"].get("native_configuration")
            if not native:
                continue
            for name, provider in native["mihomo"].get(section, {}).items():
                if provider.get("url") == url:
                    result.append({"space": space["id"], "space_name": space["name"], "name": name, "revision": snapshot["revision"]})
    return {"matches": result, "section": section, "catalog_revision": catalog.state["revision"]}


def apply(catalog, scope, request):
    with catalog.lock:
        catalog._revision(request.get("catalog_revision"))
        catalog._item(scope)
        catalog._item(request.get("source_space"))
        with catalog.resource_transaction([scope, request.get("source_space")]):
            return _apply(catalog, scope, request)


def _apply(catalog, scope, request):
    section = request.get("section")
    if section not in SECTIONS or request.get("mode") not in {"copy", "share"}:
        raise ConfigError("请选择独立复制或共享引用")
    with catalog.lock:
        catalog._revision(request.get("catalog_revision"))
        source_id = request.get("source_space")
        if source_id == scope:
            raise ConfigError("当前空间已存在该资源，可直接使用")
        source = catalog.workspace_snapshot(source_id)
        catalog.runtime(source_id).workspace._revision(request.get("source_revision"))
        target = catalog.workspace_snapshot(scope)
        catalog.runtime(scope).workspace._revision(request.get("revision"))
        native = target["profile"].get("native_configuration")
        if native is None or not source["profile"].get("native_configuration"):
            raise ConfigError("仅原生空间可建立原生资源引用")
        name = request.get("name")
        if not isinstance(name, str) or not name.strip() or any(c in name for c in ",()\n\r"):
            raise ConfigError("请填写有效资源名称")
        blob = resource(source["profile"]["native_configuration"], section, request.get("source_name"))
        if blob is None:
            raise ConfigError("来源资源已不存在")
        existing = resource(native, section, name)
        if existing is not None and existing != blob:
            raise ConfigError("当前空间有不同内容的同名资源，请更换名称")
        for kind in ("documents", "assets"):
            if any(path in native.get(kind, {}) and native[kind][path] != body for path, body in blob[kind].items()):
                raise ConfigError("缓存文件路径与当前空间已有内容冲突，请先在来源编辑器改用独立缓存路径")
        install(native, section, name, blob)
        kind = "rules" if section == "rule-providers" else None
        if kind and native.get("selection", {}).get(kind) is not None and name not in native["selection"][kind]:
            native["selection"][kind].append(name)
        result = catalog.save_profile(target["profile"], request["revision"], scope)
        if request["mode"] == "share":
            records = catalog.state.setdefault("native_resources", [])
            original = {"space": source_id, "name": request.get("source_name")}
            item = next((i for i in records if i["section"] == section and original in i["bindings"]), None)
            if item is None:
                item = {"id": uuid.uuid4().hex, "section": section, "value": blob, "bindings": [original]}
                records.append(item)
            binding = {"space": scope, "name": name}
            for old in records:
                if old is not item and old["section"] == section:
                    old["bindings"] = [b for b in old["bindings"] if b != binding]
            if binding not in item["bindings"]:
                item["bindings"].append(binding)
            catalog._write()
            # Other open clients must notice that their effective Profile changed.
            for binding in item["bindings"]:
                if binding["space"] != scope:
                    catalog.runtime(binding["space"]).workspace.probe_status("shared_resources", {"changed": True})
            result = catalog.workspace_snapshot(scope)
        return result


def reconcile(catalog, scope, profile, mode="current"):
    """Prepare ledger changes; ordinary edits create an independent copy by default."""
    records = deepcopy(catalog.state.get("native_resources", []))
    native = profile.get("native_configuration")
    if not native:
        for item in records:
            item["bindings"] = [b for b in item["bindings"] if b["space"] != scope]
        return [r for r in records if r["bindings"]]
    for item in records:
        for binding in list(item["bindings"]):
            if binding["space"] != scope:
                continue
            blob = resource(native, item["section"], binding["name"])
            if blob == item["value"]:
                continue
            if mode == "shared" and blob is not None:
                for other in item["bindings"]:
                    other_profile = catalog.workspace_snapshot(other["space"])["profile"]
                    install(other_profile["native_configuration"], item["section"], other["name"], blob)
                    Profile(other_profile)
                item["value"] = blob
            else:
                item["bindings"].remove(binding)
    return [r for r in records if r["bindings"]]


def bindings(catalog, scope):
    names = {s["id"]: s["name"] for s in catalog.state["spaces"]}
    return [{"section": r["section"], "name": b["name"], "spaces": [names.get(x["space"], x["space"]) for x in r["bindings"]]}
            for r in catalog.state.get("native_resources", []) for b in r["bindings"] if b["space"] == scope]
