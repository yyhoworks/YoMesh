"""Independent configuration spaces; the legacy workspace remains the default."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager, ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from .model import ConfigError, Profile
from .workspace import Workspace, Conflict
from .services import SubscriptionService
from .rule_sources import RuleLibraryService


class ConfigurationSpaces:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / "data"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._resource_txn_active = False
        journal = self.directory / ".resource-transaction.json"
        if journal.exists():
            recovery = json.loads(journal.read_text(encoding="utf-8"))
            self._restore_resources(recovery)
            journal.unlink()
        self.file = self.directory / "spaces.json"
        self.lock = threading.RLock()
        self.runtimes = {}
        self._generation_preview_cache = {}
        if self.file.exists():
            self.state = json.loads(self.file.read_text(encoding="utf-8"))
        else:
            self.state = {"revision": 0, "active": "default", "spaces": [{"id": "default", "name": "默认配置", "description": "原有工作区", "proxy": "mihomo", "rule_output": "inline"}], "archived": []}
            self._write()
        for item in self.state["spaces"]:
            self.runtime(item["id"])
        from .mosdns import MosDNSUpdates
        self.mosdns_updates = MosDNSUpdates(self.root, self)
        self.mosdns_updates.thread.start()

    def _write(self):
        self.state["revision"] += 1
        fd, temporary = tempfile.mkstemp(prefix=".spaces-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(self.state, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.file)
            os.chmod(self.file, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _revision(self, revision):
        if revision != self.state["revision"]:
            raise Conflict("配置空间列表已更新，请刷新后重试")

    def _item(self, ident):
        item = next((s for s in self.state["spaces"] if s["id"] == ident), None)
        if not item:
            raise ConfigError("配置空间不存在，请重新选择")
        return item

    def runtime(self, ident=None):
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            item = self._item(ident)
            if item.get("source_space"):
                return self.runtime(item["source_space"])
            if ident not in self.runtimes:
                directory = self.directory if ident == "default" else self.directory / "spaces" / ident
                workspace = Workspace(directory)
                library = RuleLibraryService(self.root, workspace)
                subscriptions = SubscriptionService(self.root, workspace, rule_library=library)
                self.runtimes[ident] = SimpleNamespace(workspace=workspace, library=library, subscriptions=subscriptions)
                subscriptions.start_scheduler()
            return self.runtimes[ident]

    def decorate(self, snapshot, ident=None):
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            item = self._item(ident)
            result = deepcopy(snapshot)
            from .group_categories import category_names, decorate_categories
            result["group_category_names"] = category_names(self.state.get("group_category_names"))
            profile = result["profile"]
            from .shared_resources import overlay, bindings
            overlay(self, profile, ident)
            result["shared_resources"] = bindings(self, ident)
            profile.update(deepcopy(item.get("overrides", {})))
            if item.get("composition"):
                profile["composition"] = deepcopy(item["composition"])
            result["generation"] = {"space": ident, "selection": deepcopy(item.get("composition", {})), "shared": bool(item.get("source_space") or ident == "default")}
            result["generation"].update(self.generation_preview(profile, (ident, snapshot["revision"], self.state["revision"])))
            result["usage"] = Profile.routing_usage(profile)
            if "native_configuration" in profile:
                from .native_configuration import overview
                result["native_view"] = overview(profile["native_configuration"])
                decorate_categories(result["native_view"], result["group_category_names"])
                result["group_summaries"] = {}
                return result
            cache = getattr(self, "_group_summary_cache", {})
            key = (id(self.runtime(ident).workspace), snapshot["revision"])
            if key not in cache:
                summary, members_by_group = {}, {}
                for group in profile.get("groups", []):
                    try:
                        members = self.runtime(ident).workspace.preview_group(profile, group["id"])["members"]
                        members_by_group[group["id"]] = members
                        summary[group["id"]] = {"count": len(members), "error": ""}
                    except ConfigError as error:
                        summary[group["id"]] = {"count": 0, "error": str(error)}
                def node_ids(ident, trail=()):
                    if ident in trail:
                        return set()
                    result = set()
                    for member in members_by_group.get(ident, []):
                        if member["protocol"] == "节点组":
                            result.update(node_ids(member["id"], (*trail, ident)))
                        else:
                            result.add(member["id"])
                    return result
                for ident, group in summary.items():
                    if not group["error"]:
                        group["count"] = len(node_ids(ident))
                self._group_summary_cache = {key: summary}
            result["group_summaries"] = self._group_summary_cache[key]
            incoming = {child for group in profile.get("groups", []) for child in group.get("groups", [])}
            group_index = {group["id"]: group for group in profile.get("groups", [])}
            def contains(parent, child, trail=()):
                if parent == child:
                    return True
                if parent in trail:
                    return False
                return any(contains(ref, child, (*trail, parent)) for ref in group_index.get(parent, {}).get("groups", []))
            result["group_management"] = []
            for group in profile.get("groups", []):
                owners = [{"id": p["id"], "name": p["name"]} for p in profile.get("policies", []) if p["target"] == group["id"]]
                section = "subscription" if group.get("sources") else "manual" if group.get("nodes") else "shared" if group["id"] in incoming else "rules" if owners else "manual"
                # Portable rules select existing groups; retain their editor here.
                category = "lines" if group.get("sources") else "home" if group.get("nodes") else "shared"
                users = [{"id": p["id"], "name": p["name"]} for p in profile.get("policies", []) if contains(p["target"], group["id"])]
                result["group_management"].append({"id": group["id"], "section": section, "category": category, "owners": owners, "used_by_rules": users})
            return result

    def generation_preview(self, profile, cache_key=None):
        from .composition import preview_composition
        if cache_key in self._generation_preview_cache:
            return deepcopy(self._generation_preview_cache[cache_key])
        try:
            if "native_configuration" in profile:
                from .native_configuration import summary
                result = summary(profile["native_configuration"])
            else:
                result = preview_composition(profile)
        except ConfigError as error:
            result = {"valid": False, "error": str(error), "counts": {}, "groups": [],
                      "excluded_policies": [], "rule_impacts": {}}
        if cache_key is not None:
            # Retain only the latest analysis per space, with no profile contents.
            self._generation_preview_cache = {k: v for k, v in self._generation_preview_cache.items() if k[0] != cache_key[0]}
            self._generation_preview_cache[cache_key] = deepcopy(result)
        return result

    def preview_selection(self, selection, revision, ident=None, blank=False):
        """Read-only preview against a specific resource revision and space."""
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            workspace = self.runtime(ident).workspace
            with workspace.lock:
                workspace._revision(revision)
                profile = deepcopy(workspace.state["profile"])
                profile.update(deepcopy(self._item(ident).get("overrides", {})))
                if blank:
                    profile["inbounds"] = []
                profile["composition"] = selection
                from .composition import preview_composition
                return {"revision": revision, **preview_composition(profile)}

    def workspace_snapshot(self, ident=None):
        return self.decorate(self.runtime(ident).workspace.snapshot(), ident)

    @staticmethod
    def _atomic_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".resource-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _restore_resources(self, journal):
        self._atomic_json(self.directory / "spaces.json", journal["catalog"])
        for ident, state in journal["workspaces"].items():
            if ident != "default" and not __import__("re").fullmatch(r"space_[a-f0-9]{32}", ident):
                raise ConfigError("资源事务恢复目录无效")
            folder = self.directory if ident == "default" else self.directory / "spaces" / ident
            self._atomic_json(folder / "workspace.json", state)

    @contextmanager
    def resource_transaction(self, ids):
        with self.lock, ExitStack() as stack:
            if self._resource_txn_active:
                yield
                return
            owners = set()
            for ident in ids:
                owner = self._item(ident)
                while owner.get("source_space"):
                    owner = self._item(owner["source_space"])
                owners.add(owner["id"])
            states = {}
            for ident in sorted(owners):
                workspace = self.runtime(ident).workspace
                stack.enter_context(workspace.lock)
                states[ident] = deepcopy(workspace.state)
            recovery = {"catalog": deepcopy(self.state), "workspaces": states}
            journal = self.directory / ".resource-transaction.json"
            self._atomic_json(journal, recovery)
            self._resource_txn_active = True
            try:
                yield
            except Exception:
                self.state = recovery["catalog"]
                for ident, state in states.items():
                    self.runtime(ident).workspace.state = state
                self._generation_preview_cache.clear()
                self._group_summary_cache = {}
                self._restore_resources(recovery)
                journal.unlink()
                raise
            else:
                journal.unlink()
            finally:
                self._resource_txn_active = False

    def save_profile(self, value, revision, ident=None, share_scope="current"):
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            records = self.state.get("native_resources", [])
            if self._resource_txn_active:
                return self._save_profile(value, revision, ident, share_scope)
            ids = [ident, *(b["space"] for r in records for b in r["bindings"])]
            with self.resource_transaction(ids):
                return self._save_profile(value, revision, ident, share_scope)

    def _save_profile(self, value, revision, ident=None, share_scope="current"):
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            item = self._item(ident)
            workspace = self.runtime(ident).workspace
            with workspace.lock:
                workspace._revision(revision)
                candidate = deepcopy(value)
                candidate.pop("composition", None)
                workspace._validate_profile(candidate)
                from .shared_resources import reconcile
                if share_scope not in {"current", "shared"}:
                    raise ConfigError("请选择当前空间独立修改或同步共享资源")
                ledger = reconcile(self, ident, candidate, share_scope)
                overrides = {k: deepcopy(candidate.get(k, {} if k != "inbounds" else [])) for k in ("settings", "base_configs", "inbounds")}
                if "mosdns" in candidate:
                    overrides["mosdns"] = deepcopy(candidate["mosdns"])
                if item.get("source_space"):
                    for key in overrides:
                        if key in workspace.state["profile"]:
                            candidate[key] = deepcopy(workspace.state["profile"][key])
                        else:
                            candidate.pop(key, None)
                if candidate == workspace.state["profile"]:
                    workspace._write()
                    result = workspace.snapshot()
                else:
                    result = workspace.save_profile(candidate, revision)
                if item.get("source_space"):
                    item["overrides"] = overrides
                    self._write()
                if ledger != self.state.get("native_resources", []):
                    self.state["native_resources"] = ledger
                    self._write()
                    if share_scope == "shared":
                        for record in ledger:
                            for binding in record["bindings"]:
                                if binding["space"] != ident:
                                    self.runtime(binding["space"]).workspace.probe_status("shared_resources", {"changed": True})
                return self.decorate(result, ident)

    def save_settings(self, settings, revision, ident=None):
        snapshot = self.workspace_snapshot(ident)
        snapshot["profile"]["settings"] = settings
        return self.save_profile(snapshot["profile"], revision, ident)

    def save_mosdns(self, config, revision, ident=None):
        """A DNS edit cannot normalize or rewrite any proxy document."""
        from .mosdns import checked
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            item = self._item(ident)
            workspace = self.runtime(ident).workspace
            with workspace.lock:
                workspace._revision(revision)
                settings = item.get("overrides", {}).get("settings", workspace.state["profile"].get("settings", {}))
                normalized = checked(config, settings)
                owner = item
                while owner.get("source_space"):
                    owner = self._item(owner["source_space"])
                with self.resource_transaction([owner["id"]]):
                    if item.get("source_space"):
                        item.setdefault("overrides", {})["mosdns"] = normalized
                        self._write()
                    else:
                        workspace.state["profile"]["mosdns"] = normalized
                    workspace._write()
                return self.workspace_snapshot(ident)

    def save_base_config(self, target, text, revision, ident=None):
        from .base_config import prepare_base_profile
        snapshot = self.workspace_snapshot(ident)
        return self.save_profile(prepare_base_profile(snapshot["profile"], target, text), revision, ident)

    def save_selection(self, selection, revision, ident=None):
        from .composition import validate_selection
        with self.lock:
            ident = self.state["active"] if ident is None else ident
            workspace = self.runtime(ident).workspace
            with workspace.lock:
                workspace._revision(revision)
                cleaned = validate_selection(selection, workspace.state["profile"])
                self._item(ident)["composition"] = cleaned
                self._write()
                workspace._write()
                return self.workspace_snapshot(ident)

    def snapshot(self):
        with self.lock:
            result = {k: deepcopy(self.state[k]) for k in ("revision", "active", "spaces")}
            from .group_categories import category_names
            result["group_category_names"] = category_names(self.state.get("group_category_names"))
            for item in result["spaces"]:
                item.pop("overrides", None)
                workspace = self.runtime(item["id"]).workspace
                with workspace.lock:
                    profile = workspace.state["profile"]
                    item["counts"] = {key: len(profile.get(key, [])) for key in ("sources", "nodes", "groups", "library", "policies", "inbounds")}
                    for selection, count in (("groups", "groups"), ("rules", "library"), ("policies", "policies")):
                        if item.get("composition", {}).get(selection) is not None:
                            item["counts"][count] = len(item["composition"][selection])
                    raw = deepcopy(profile)
                    from .shared_resources import overlay
                    overlay(self, raw, item["id"])
                    raw.update(deepcopy(self._item(item["id"]).get("overrides", {})))
                    raw["composition"] = item.get("composition", {})
                    generation = self.generation_preview(raw, (item["id"], workspace.state["revision"], self.state["revision"]))
                    from .dns_engine import engine_for
                    item["dns_engine"] = engine_for(raw)
                    if "native_configuration" in raw:
                        item["native"] = True
                        item["counts"].update(generation["counts"])
                    item["generation"] = {k: generation[k] for k in ("valid", "counts", "error", "excluded_policies")}
                    if generation["valid"]:
                        item["counts"].update(generation["counts"])
                    elif generation["error"]:
                        item["generation_error"] = generation["error"]
            return result

    def save_group_category_names(self, value, revision):
        from .group_categories import category_names
        if value is None:
            raise ConfigError("请填写分类显示名称")
        names = category_names(value)
        with self.lock:
            self._revision(revision)
            before = deepcopy(self.state)
            try:
                self.state["group_category_names"] = names
                self._write()
            except Exception:
                self.state = before
                raise
            return self.snapshot()

    def import_native(self, native, name, revision, description="", copy_from=None, mosdns=None):
        """Create an isolated configuration without touching existing resources."""
        from .workspace import empty_profile
        from .native_configuration import checked
        with self.lock:
            self._revision(revision)
            checked(native)
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ConfigError("名称需要 1–80 个字符")
            if any(s["name"] == name.strip() for s in self.state["spaces"]):
                raise ConfigError("配置空间名称已存在")
            if len(self.state["spaces"]) >= 32:
                raise ConfigError("最多保存 32 个配置空间")
            ident = "space_" + uuid.uuid4().hex
            profile = empty_profile()
            profile.update(groups=[], fallback="DIRECT", native_configuration=deepcopy(native))
            if mosdns is not None:
                from .mosdns import checked as checked_mosdns
                profile["mosdns"] = checked_mosdns(mosdns)
            workspace = Workspace(self.directory / "spaces" / ident)
            workspace.state.update(profile=Profile(profile).data, migrated=True)
            workspace._write()
            self.state["spaces"].append({"id": ident, "name": name.strip(), "description": str(description)[:500], "proxy": "mihomo", "rule_output": "remote", "native": True})
            self._write()
            return {"id": ident, **self.snapshot()}

    def save(self, value, revision, ident=None, copy_from=None, workspace_revision=None):
        with self.lock, self.runtime(ident or copy_from or "default").workspace.lock:
            self._revision(revision)
            if workspace_revision is not None:
                self.runtime(ident or copy_from or "default").workspace._revision(workspace_revision)
            if not isinstance(value, dict):
                raise ConfigError("配置空间需要对象")
            name, description = value.get("name", ""), value.get("description", "")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
                raise ConfigError("名称需要 1–80 个字符")
            if not isinstance(description, str) or len(description) > 500:
                raise ConfigError("说明最多 500 个字符")
            if any(s["name"] == name.strip() and s["id"] != ident for s in self.state["spaces"]):
                raise ConfigError("配置空间名称已存在")
            proxy, mode = value.get("proxy", "mihomo"), value.get("rule_output", "inline")
            if proxy not in {"mihomo", "sing-box"} or mode not in {"remote", "local", "inline"}:
                raise ConfigError("配置内核或规则输出方式无效")
            selection = None
            if "composition" in value:
                from .composition import validate_selection
                selection = validate_selection(value["composition"], self.runtime(ident or copy_from or "default").workspace.snapshot()["profile"])
            if ident is None:
                if len(self.state["spaces"]) >= 32:
                    raise ConfigError("最多保存 32 个配置空间")
                source_id = copy_from or "default"
                source = self.workspace_snapshot(source_id)
                source_item = self._item(source_id)
                ident = "space_" + uuid.uuid4().hex
                item = {"id": ident, "source_space": source_item.get("source_space", source_id), "composition": deepcopy(source_item.get("composition", {})), "overrides": {k: deepcopy(source["profile"].get(k, {} if k != "inbounds" else [])) for k in ("settings", "base_configs", "inbounds")}}
                if "mosdns" in source["profile"]:
                    item["overrides"]["mosdns"] = deepcopy(source["profile"]["mosdns"])
                if copy_from is None:
                    item["composition"] = {"groups": [], "rules": [], "policies": [], "fallback": "DIRECT"}
                    item["overrides"]["inbounds"] = []
                self.state["spaces"].append(item)
            else:
                item = self._item(ident)
            item.update(name=name.strip(), description=description.strip(), proxy=proxy, rule_output=mode)
            if selection is not None:
                item["composition"] = selection
            self._write()
            if selection is not None:
                workspace = self.runtime(item["id"]).workspace
                with workspace.lock:
                    workspace._write()
            return {"id": ident, **self.snapshot()}

    def select(self, ident, revision):
        with self.lock:
            self._revision(revision)
            self._item(ident)
            self.state["active"] = ident
            self._write()
            return self.snapshot()

    def delete(self, ident, revision):
        with self.lock:
            self._revision(revision)
            item = self._item(ident)
            if ident == "default" or ident == self.state["active"]:
                raise ConfigError("默认或使用中的配置空间不能删除，请先切换到其他空间")
            if any(s.get("source_space") == ident for s in self.state["spaces"]):
                raise ConfigError("此空间仍为其他生成方案提供共享资源，不能删除")
            runtime = self.runtimes.get(ident)
            if runtime:
                runtime.subscriptions.stop.set()
            self.state["spaces"].remove(item)
            for record in self.state.get("native_resources", []):
                record["bindings"] = [b for b in record["bindings"] if b["space"] != ident]
            if "native_resources" in self.state:
                self.state["native_resources"] = [r for r in self.state["native_resources"] if r["bindings"]]
            # Preserve files for recovery; no recursive filesystem deletion.
            self.state.setdefault("archived", []).append(item)
            self._write()
            return self.snapshot()

    def close(self):
        self.mosdns_updates.stop.set()
        with self.lock:
            for runtime in self.runtimes.values():
                runtime.subscriptions.stop.set()
