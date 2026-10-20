"""Persistent workspace and source settings. Updates are atomic and revisioned."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .model import ConfigError, Profile, pattern
from .subscriptions import FORMATS


class Conflict(ConfigError):
    pass


def next_update(source, now=None):
    now = time.time() if now is None else now
    if not source.get("enabled", True) or not source.get("auto_update", True):
        return None
    if source.get("schedule_mode") == "daily":
        hour, minute = map(int, source.get("daily_time", "04:00").split(":"))
        # User scene is Asia/Shanghai regardless of the container's UTC timezone.
        local = datetime.fromtimestamp(now, timezone(timedelta(hours=8)))
        next_time = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if next_time <= local:
            next_time += timedelta(days=1)
        return next_time.timestamp()
    return now + source.get("interval_hours", 24) * 3600


def clean_source(value, existing=None):
    if not isinstance(value, dict):
        raise ConfigError("订阅设置需要为对象")
    existing = existing or {}
    source = {"id": existing.get("id") or value.get("id") or "s_" + uuid.uuid4().hex[:12]}
    if not isinstance(source["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source["id"]) or source["id"] in {"manual", "DIRECT", "REJECT"}:
        raise ConfigError("订阅 ID 无效")
    for key, default in {"name": "", "url": "", "note": "", "format": "auto", "user_agent": "clash.meta", "include": "", "exclude": "", "disabled_protocols": [], "enabled": True, "auto_update": True, "interval_hours": 24, "schedule_mode": "interval", "daily_time": "04:00", "test_url": "https://www.gstatic.com/generate_204", "test_timeout_ms": 3000, "request_timeout": 20}.items():
        source[key] = deepcopy(value.get(key, existing.get(key, default)))
    if not isinstance(source["name"], str) or not source["name"].strip() or len(source["name"]) > 80:
        raise ConfigError("订阅名称需要为 1–80 字符")
    source["name"] = source["name"].strip()
    if not isinstance(source["url"], str) or len(source["url"]) > 8192:
        raise ConfigError("订阅地址无效")
    url = urlsplit(source["url"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        raise ConfigError("订阅需要 HTTP/HTTPS 地址，不支持 URL 中的用户名密码")
    if not isinstance(source["format"], str) or source["format"] not in FORMATS:
        raise ConfigError("未知订阅格式")
    for key in ("include", "exclude"):
        if not isinstance(source[key], str) or len(source[key]) > 500:
            raise ConfigError("正则最多 500 字符")
        pattern(source[key], key)
    if not isinstance(source["disabled_protocols"], list) or not all(isinstance(x, str) for x in source["disabled_protocols"]):
        raise ConfigError("禁用协议需要文本数组")
    for key in ("enabled", "auto_update"):
        if not isinstance(source[key], bool):
            raise ConfigError("启用开关需要布尔值")
    for key, low, high in (("interval_hours", 1, 720), ("request_timeout", 3, 120), ("test_timeout_ms", 500, 30000)):
        if type(source[key]) is not int or not low <= source[key] <= high:
            raise ConfigError(f"{key} 需要在 {low}–{high} 范围内")
    if source["schedule_mode"] not in {"interval", "daily"}:
        raise ConfigError("更新计划仅支持 interval / daily")
    try:
        datetime.strptime(source["daily_time"], "%H:%M")
    except (ValueError, TypeError):
        raise ConfigError("固定时刻需要为 HH:MM") from None
    for key in ("user_agent", "note"):
        if not isinstance(source[key], str) or len(source[key]) > 1000 or (key == "user_agent" and any(x in source[key] for x in ("\r", "\n"))):
            raise ConfigError(f"{key} 内容无效")
    if not isinstance(source["test_url"], str) or len(source["test_url"]) > 8192:
        raise ConfigError("测速 URL 无效")
    test = urlsplit(source["test_url"])
    if test.scheme not in {"http", "https"} or not test.hostname or test.username or test.password:
        raise ConfigError("测速 URL 需要为 HTTP/HTTPS")
    return source


def empty_profile():
    return {"schema_version": 1, "name": "YoMesh", "sources": [], "nodes": [], "groups": [{"id": "g_default", "name": "默认代理", "mode": "select", "sources": [], "nodes": [], "groups": [], "include": "", "exclude": "", "interval": 300}], "library": [], "policies": [], "fallback": "g_default", "settings": {}}


class Workspace:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.file = self.directory / "workspace.json"
        self.lock = threading.RLock()
        if self.file.exists():
            self.state = json.loads(self.file.read_text(encoding="utf-8"))
        else:
            self.state = {"revision": 0, "profile": empty_profile(), "source_status": {}, "node_status": {}, "migrated": False}
        # A restart must not leave orphaned jobs appearing to run forever.
        statuses = [*self.state["source_status"].values(), *self.state.get("library_status", {}).values(), *self.state.get("group_status", {}).values()]
        for status in statuses:
            for key in ("refresh_state", "test_state"):
                if status.get(key) == "running":
                    status[key] = "interrupted"

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.state)
            result["usage"] = Profile.routing_usage(result["profile"])
            return result

    def revision(self):
        with self.lock:
            return self.state["revision"]

    def due_updates(self, now=None):
        """Return job IDs without copying rules, cached files or credentials."""
        now = time.time() if now is None else now
        with self.lock:
            sources = []
            for source in self.state["profile"].get("sources", []):
                due = self.state.get("source_status", {}).get(source["id"], {}).get("next_update")
                if source.get("enabled", True) and source.get("auto_update", True) and due and due <= now:
                    sources.append(source["id"])
            rules = []
            for item in self.state["profile"].get("library", []):
                due = self.state.get("library_status", {}).get(item["id"], {}).get("next_update")
                if due and due <= now and item.get("source", {}).get("auto_update"):
                    rules.append(item["id"])
            return {"sources": sources, "rules": rules}

    def _write(self):
        self.state["revision"] += 1
        fd, temporary = tempfile.mkstemp(prefix=".workspace-", suffix=".json", dir=self.directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(self.state, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            if self.file.exists():
                shutil.copyfile(self.file, self.directory / "workspace.backup.json")
                os.chmod(self.directory / "workspace.backup.json", 0o600)
            os.replace(temporary, self.file)
            os.chmod(self.file, 0o600)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _revision(self, revision):
        if revision != self.state["revision"]:
            raise Conflict("服务器数据已更新，请刷新工作区后再保存")

    def source(self, ident):
        for source in self.state["profile"]["sources"]:
            if source["id"] == ident:
                return source
        raise ConfigError("订阅不存在")

    def save_source(self, value, ident=None, revision=None):
        with self.lock:
            self._revision(revision)
            previous = self.source(ident) if ident else None
            source = clean_source(value, previous)
            if previous is None and any(s["id"] == source["id"] for s in self.state["profile"]["sources"]):
                raise ConfigError("订阅 ID 已存在")
            if any(s["name"] == source["name"] and s["id"] != source["id"] for s in self.state["profile"]["sources"]):
                raise ConfigError("订阅名称已存在")
            if previous:
                previous.update(source)
            else:
                self.state["profile"]["sources"].append(source)
                default = next((g for g in self.state["profile"]["groups"] if g["id"] == "g_default"), None)
                if default:
                    default["sources"].append(source["id"])
            status = self.state["source_status"].setdefault(source["id"], {})
            status["next_update"] = next_update(source)
            self._write()
            return source["id"]

    def import_legacy(self, values):
        with self.lock:
            if self.state["profile"]["sources"] or self.state["migrated"]:
                return self.snapshot()
            used_ids, used_names = set(), set()
            for value in values[:100]:
                try:
                    hostname = urlsplit(value.get("url", "")).hostname or ""
                    if hostname.endswith(".example") or not hostname:
                        continue
                    source = clean_source(value)
                    if source["id"] in used_ids or source["name"] in used_names:
                        continue
                    used_ids.add(source["id"])
                    used_names.add(source["name"])
                    self.state["profile"]["sources"].append(source)
                    self.state["profile"]["groups"][0]["sources"].append(source["id"])
                    self.state["source_status"][source["id"]] = {"next_update": None}
                except (ConfigError, ValueError, TypeError, AttributeError):
                    continue
            self.state["migrated"] = True
            self._write()
            return self.snapshot()

    def save_profile(self, value, revision):
        with self.lock:
            self._revision(revision)
            candidate = self._validate_profile(value)
            self.state["profile"] = candidate
            active = candidate.get("active_plan")
            for plan in candidate.get("routing_plans", []):
                if plan["id"] == active:
                    plan.update(policies=deepcopy(candidate["policies"]), fallback=candidate["fallback"])
            self.state["routing_plans"] = deepcopy(candidate.get("routing_plans", []))
            self.state["active_plan"] = active
            self._write()
            return self.snapshot()

    def _validate_profile(self, value, sources=None):
        with self.lock:
            if not isinstance(value, dict):
                raise ConfigError("工作区需要为对象")
            candidate = deepcopy(value)
            candidate["schema_version"] = 1
            if "native_configuration" in candidate:
                # Native spaces are independent documents, validated through
                # Profile and the original target cores, never the portable subset.
                return Profile(candidate).data
            # Subscription mutations must use the source interface, never overwrite
            # fresher server snapshots from a stale browser copy.
            candidate["sources"] = deepcopy(self.state["profile"]["sources"] if sources is None else sources)
            for plan in candidate.get("routing_plans", []):
                if plan["id"] == candidate.get("active_plan"):
                    plan.update(policies=deepcopy(candidate["policies"]), fallback=candidate["fallback"])
            for kind in ("nodes", "groups", "library", "policies"):
                if not isinstance(candidate.get(kind), list):
                    raise ConfigError(f"{kind} 需要数组")
            # Validate node parameters even while empty group drafts may be saved.
            wrapper = {"schema_version": 1, "sources": candidate["sources"], "nodes": candidate["nodes"], "library": candidate["library"], "fallback": "DIRECT", "settings": candidate.get("settings", {}), "base_configs": candidate.get("base_configs", {})}
            if "mosdns" in candidate:
                wrapper["mosdns"] = candidate["mosdns"]
            validated = Profile(wrapper)
            if "mosdns" in validated.data:
                candidate["mosdns"] = validated.data["mosdns"]
            candidate["nodes"] = validated.data["nodes"]
            seen = set()
            for group in candidate["groups"]:
                ident = group.get("id")
                if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", ident) or ident in seen or ident in validated.index["nodes"]:
                    raise ConfigError("节点组 ID 不合法或重复")
                if not isinstance(group.get("name"), str) or not group["name"].strip():
                    raise ConfigError("节点组名称不能为空")
                seen.add(ident)
            validated.index["groups"] = {g["id"]: g for g in candidate["groups"]}
            for group in candidate["groups"]:
                validated._group(group)
                try:
                    validated.members(group["id"])
                except ConfigError as exc:
                    if "没有有效成员" not in str(exc):
                        raise
            # Missing targets can be kept as an explicitly invalid draft only when
            # sources are disabled; missing IDs and invalid rules never persist.
            for policy in candidate["policies"]:
                validated._policy(policy)
            if candidate.get("fallback") not in {"DIRECT", *seen, *validated.index["nodes"]}:
                raise ConfigError("兜底出口不存在")
            validated.validate_extensions(candidate)
            from .base_config import effective_settings
            for target in validated.base_configs:
                effective_settings(validated, target, require_socks=False)
            return candidate

    def base_config_editor(self):
        from .base_config import base_editor
        with self.lock:
            value = self.state["profile"]
            return base_editor(Profile({"schema_version": 1, "fallback": "DIRECT", "settings": value.get("settings", {}), "base_configs": value.get("base_configs", {})}))

    def save_base_config(self, target, text, revision):
        from .base_config import parse_base
        parse_base(text, target)
        with self.lock:
            self._revision(revision)
            value = deepcopy(self.state["profile"])
            value.setdefault("base_configs", {})[target] = text
            return self.save_profile(value, revision)

    def ensure_plan(self, revision):
        with self.lock:
            self._revision(revision)
            value = deepcopy(self.state["profile"])
            if value.get("routing_plans"):
                return self.snapshot()
            value["active_plan"] = "default"
            value["routing_plans"] = [{"id": "default", "name": "default", "policies": deepcopy(value["policies"]), "fallback": value["fallback"]}]
            return self.save_profile(value, revision)

    def import_configuration(self, value, revision):
        with self.lock:
            self._revision(revision)
            if not isinstance(value, dict):
                raise ConfigError("迁移配置需要对象")
            raw_profile = deepcopy(value.get("profile"))
            if not isinstance(raw_profile, dict):
                raise ConfigError("迁移缺少完整 profile")
            plans = deepcopy(value.get("routing_plans", []))
            if not isinstance(plans, list) or not plans:
                raise ConfigError("迁移需要规则方案")
            seen = set()
            for plan in plans:
                if not isinstance(plan, dict) or not isinstance(plan.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", plan["id"]) or plan["id"] in seen or not isinstance(plan.get("name"), str):
                    raise ConfigError("迁移方案 ID 或名称无效")
                seen.add(plan["id"])
            active = value.get("active_plan")
            if active not in seen:
                raise ConfigError("当前规则方案不存在")
            chosen = next(p for p in plans if p["id"] == active)
            raw_profile.update(policies=deepcopy(chosen["policies"]), fallback=chosen["fallback"], routing_plans=plans, active_plan=active)
            sources = [clean_source(source) for source in raw_profile.get("sources", [])]
            if len({s["id"] for s in sources}) != len(sources) or len({s["name"] for s in sources}) != len(sources):
                raise ConfigError("迁移订阅 ID 或名称重复")
            if not {s["id"] for s in self.state["profile"]["sources"]}.issubset({s["id"] for s in sources}):
                raise ConfigError("迁移不能移除工作区原有订阅")
            candidate = self._validate_profile(raw_profile, sources=sources)
            # Backup is separate from the rotating previous-save snapshot.
            if self.file.exists():
                backup = self.directory / ("workspace.before-mr-" + uuid.uuid4().hex[:12] + ".json")
                shutil.copyfile(self.file, backup)
                os.chmod(backup, 0o600)
            self.state.update(profile=candidate, routing_plans=plans, active_plan=active)
            self.state["migration_report"] = deepcopy(value.get("report", {}))
            for source in sources:
                count = sum(n.get("source") == source["id"] for n in candidate["nodes"])
                old = self.state["source_status"].get(source["id"], {})
                previous_count = old.get("retained", count)
                self.state["source_status"][source["id"]] = {**old, "detected_format": source["format"], "retained": count, "total": max(count, previous_count), "filtered": max(0, previous_count - count), "skipped": 0, "issues": [], "refresh_state": "success", "error": "", "next_update": next_update(source), "test_state": "not-tested", "test_error": ""}
                for key in ("tested_at", "test_success", "test_failed", "test_finished", "test_total"):
                    self.state["source_status"][source["id"]].pop(key, None)
            self.state["node_status"] = {}
            self._write()
            return self.snapshot()

    def switch_plan(self, ident, revision):
        with self.lock:
            self._revision(revision)
            plan = next((p for p in self.state.get("routing_plans", []) if p["id"] == ident), None)
            if not plan:
                raise ConfigError("规则方案不存在")
            candidate = {**self.state["profile"], "policies": plan["policies"], "fallback": plan["fallback"], "active_plan": ident}
            self.state["profile"] = self._validate_profile(candidate)
            self.state["active_plan"] = ident
            self._write()
            return self.snapshot()

    def pinned_targets(self):
        profile = self.state["profile"]
        plans = profile.get("routing_plans", [])
        return {profile.get("fallback"), *(p["target"] for p in profile["policies"]), *(plan["fallback"] for plan in plans), *(p["target"] for plan in plans for p in plan["policies"])}

    def library_status(self, ident, status):
        with self.lock:
            if not any(l["id"] == ident for l in self.state["profile"]["library"]):
                return
            self.state.setdefault("library_status", {}).setdefault(ident, {}).update(status)
            self._write()

    def save_library(self, item, status, revision):
        return self.save_libraries([item], [status], revision)

    def save_libraries(self, items, statuses, revision):
        with self.lock:
            self._revision(revision)
            candidate = deepcopy(self.state["profile"])
            if len({i["id"] for i in items}) != len(items):
                raise ConfigError("规则集 ID 不能重复")
            for item in items:
                if any(l["name"] == item["name"] and l["id"] != item["id"] for l in candidate["library"]):
                    raise ConfigError("规则集名称已存在")
                old = next((l for l in candidate["library"] if l["id"] == item["id"]), None)
                if old:
                    candidate["library"][candidate["library"].index(old)] = item
                else:
                    candidate["library"].append(item)
            validated = self._validate_profile(candidate)
            self.state["profile"] = validated
            self.state.setdefault("library_status", {}).update({item["id"]: status for item, status in zip(items, statuses)})
            self._write()
            return self.snapshot()

    def replace_library(self, item, status, expected):
        with self.lock:
            current = next((l for l in self.state["profile"]["library"] if l["id"] == item["id"]), None)
            if current != expected:
                return False
            self.save_library(item, status, self.revision())
            return True

    def delete_library(self, ident, revision):
        from .rulelogic import references
        with self.lock:
            self._revision(revision)
            policies = [*self.state["profile"]["policies"], *(p for plan in self.state["profile"].get("routing_plans", []) for p in plan["policies"])]
            if any(ident in references(policy) for policy in policies):
                raise ConfigError("规则集正被规则引用，请先移除对应条件；其他方案也会检查")
            if not any(l["id"] == ident for l in self.state["profile"]["library"]):
                raise ConfigError("规则集不存在")
            self.state["profile"]["library"] = [l for l in self.state["profile"]["library"] if l["id"] != ident]
            self.state.setdefault("library_status", {}).pop(ident, None)
            self._write()
            return self.snapshot()

    def preview_rule(self, value, policy):
        from .rulelogic import expression, describe, has_ip
        wrapper = {"schema_version": 1, "sources": value.get("sources", []), "nodes": value.get("nodes", []), "library": value.get("library", []), "fallback": "DIRECT"}
        validated = Profile(wrapper)
        validated.index["groups"] = {g["id"]: g for g in value.get("groups", [])}
        validated._policy(policy)
        expr = expression(policy, validated.index["library"])
        groups = policy.get("condition_groups", [])
        return {"summary": describe(expr, validated.index["library"]), "groups": len(groups) or 1, "conditions": sum(len(g["conditions"]) for g in groups) or 1, "has_ip": has_ip(expr, validated)}

    def probe_status(self, ident, status):
        with self.lock:
            self.state.setdefault("group_status", {}).setdefault(ident, {}).update(status)
            self._write()

    def preview_group(self, value, ident):
        candidate = deepcopy(value)
        wrapper = {"schema_version": 1, "sources": candidate.get("sources", []), "nodes": candidate.get("nodes", []), "fallback": "DIRECT"}
        validated = Profile(wrapper)
        validated.index["groups"] = {g["id"]: g for g in candidate.get("groups", [])}
        if ident not in validated.index["groups"]:
            raise ConfigError("节点组不存在")
        for group in validated.index["groups"].values():
            validated._group(group)
        result = validated.members(ident, allow_empty=True)
        return {"members": [{"id": i, "name": (validated.index["nodes"].get(i) or validated.index["groups"].get(i))["name"], "protocol": validated.index["nodes"].get(i, {}).get("protocol", "节点组")} for i in result]}

    def delete_source(self, ident, revision):
        with self.lock:
            self._revision(revision)
            self.source(ident)
            profile = self.state["profile"]
            removed = {n["id"] for n in profile["nodes"] if n.get("source") == ident}
            if self.pinned_targets() & removed:
                raise ConfigError("策略直接引用了该订阅的节点，请先更换对应出口再删除")
            profile["sources"] = [s for s in profile["sources"] if s["id"] != ident]
            profile["nodes"] = [n for n in profile["nodes"] if n["id"] not in removed]
            for group in profile["groups"]:
                group["sources"] = [s for s in group["sources"] if s != ident]
                group["nodes"] = [n for n in group["nodes"] if n not in removed]
                if "order" in group:
                    group["order"] = [n for n in group["order"] if n not in removed]
                if group.get("selected") in removed:
                    group.pop("selected")
            self.state["source_status"].pop(ident, None)
            for node in removed:
                self.state["node_status"].pop(node, None)
            self._write()
            return self.snapshot()

    def reorder_sources(self, ids, revision):
        with self.lock:
            self._revision(revision)
            previous = {s["id"]: s for s in self.state["profile"]["sources"]}
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) != len(previous) or set(ids) != set(previous):
                raise ConfigError("订阅排序需要恰好包含所有来源")
            self.state["profile"]["sources"] = [previous[i] for i in ids]
            self._write()
            return self.snapshot()

    def reorder_policies(self, ids, plan, revision):
        with self.lock:
            self._revision(revision)
            profile = self.state["profile"]
            if plan != profile.get("active_plan"):
                raise Conflict("当前规则方案已变化，请刷新后再排序")
            previous = {p["id"]: p for p in profile["policies"]}
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) != len(previous) or set(ids) != set(previous):
                raise ConfigError("策略排序需要恰好包含所有普通规则，兜底不参与排序")
            candidate = deepcopy(profile)
            candidate["policies"] = [previous[i] for i in ids]
            return self.save_profile(candidate, revision)

    def save_settings(self, settings, revision):
        with self.lock:
            self._revision(revision)
            if not isinstance(settings, dict):
                raise ConfigError("DNS 设置需要对象")
            resolved = Profile({"schema_version": 1, "fallback": "DIRECT", "settings": settings}).settings
            candidate = deepcopy(self.state["profile"])
            candidate["settings"] = resolved
            return self.save_profile(candidate, revision)

    def preview_dns(self, settings, value=None):
        from .compiler import compile_mosdns
        with self.lock:
            candidate = deepcopy(self.state["profile"] if value is None else value)
        candidate["settings"] = settings
        from .composition import resolve_composition
        candidate, _ = resolve_composition(candidate, include_proxy=False)
        profile = Profile.for_dns(candidate)
        config, diagnostics = compile_mosdns(profile)
        return {"config": config, "diagnostics": diagnostics, "scope": Profile.routing_usage(candidate)["dns_scope"] if profile.settings["dns_follow_rules"] else "未跟随代理规则；所有域名使用 DNS 默认上游"}

    def import_nodes(self, text, revision):
        from .subscriptions import parse_subscription, normalize
        with self.lock:
            self._revision(revision)
            if not isinstance(text, str):
                raise ConfigError("节点内容需要为文本")
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict) and "protocol" in parsed:
                nodes = [normalize(parsed, "manual")]
            else:
                nodes, _ = parse_subscription(text, {"id": "manual", "format": "auto"})
            if not nodes:
                raise ConfigError("没有可导入的节点")
            existing = {n["id"]: n for n in self.state["profile"]["nodes"]}
            existing.update({n["id"]: n for n in nodes})
            self.state["profile"]["nodes"] = list(existing.values())
            default = next((g for g in self.state["profile"]["groups"] if g["id"] == "g_default"), None)
            if default:
                default["nodes"] = list(dict.fromkeys(default["nodes"] + [n["id"] for n in nodes]))
            self._write()
            return self.snapshot()

    def status(self, ident, values):
        with self.lock:
            self.source(ident)
            self.state["source_status"].setdefault(ident, {}).update(values)
            self._write()

    def replace_nodes(self, source, nodes, stats):
        with self.lock:
            if self.source(source["id"]) != source:
                raise Conflict("拉取期间订阅设置已变更，本次结果未写入，请重新刷新")
            profile = self.state["profile"]
            old_nodes = {n["id"]: n for n in profile["nodes"] if n.get("source") == source["id"]}
            new_ids = {n["id"] for n in nodes}
            lost = set(old_nodes) - new_ids
            pinned = self.pinned_targets()
            if lost & pinned:
                raise ConfigError("更新会移除策略直接引用的节点；请先更换该出口，原节点已保留")
            for node in nodes:
                if node["id"] in old_nodes:
                    node["enabled"] = old_nodes[node["id"]].get("enabled", True)
            profile["nodes"] = [n for n in profile["nodes"] if n.get("source") != source["id"]] + nodes
            for group in profile["groups"]:
                group["nodes"] = [n for n in group["nodes"] if n not in lost]
                if "order" in group:
                    group["order"] = [n for n in group["order"] if n not in lost]
                if group.get("selected") in lost:
                    group.pop("selected")
            for node in lost:
                self.state["node_status"].pop(node, None)
            self.state["source_status"].setdefault(source["id"], {}).update({**stats, "refresh_state": "success", "last_updated": time.time(), "last_attempt": time.time(), "error": "", "next_update": next_update(source)})
            self._write()

    def node_result(self, source_id, node_id, result, finished, total):
        with self.lock:
            if not any(n["id"] == node_id for n in self.state["profile"]["nodes"]):
                return
            self.state["node_status"][node_id] = {**result, "tested_at": time.time()}
            self.state["source_status"].setdefault(source_id, {}).update({"test_finished": finished, "test_total": total})
            if finished % 10 == 0 or finished == total:
                self._write()
