"""Bounded imports, native probes and read-only interaction endpoints."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import quote, urlencode, urlsplit

from .model import ConfigError, Profile
from .interaction_tools import node_catalog, native_core, preview_group, group_filter_view, diagnose, prepare_import, repair_reference
from .interaction_inspection import scan_rule_entries
from .interaction_probe import measure_node, record_probe_result
from .native import validate_bundle
from .compiler import compile_bundle


class InteractionService:
    def __init__(self, root, spaces):
        self.root, self.spaces = Path(root), spaces
        self.pending = {}
        self.lock = threading.RLock()
        self.scan_slot = threading.BoundedSemaphore(1)

    def handle(self, scope, request):
        action = request.get("action")
        if action == "resource-find":
            from .shared_resources import listing
            return listing(self.spaces, request)
        if action == "resource-apply":
            from .shared_resources import apply
            return {"workspace": apply(self.spaces, scope, request)}
        runtime = self.spaces.runtime(scope)
        snapshot = self.spaces.workspace_snapshot(scope)
        native = snapshot["profile"].get("native_configuration")
        if not native:
            raise ConfigError("请在原生配置空间使用此工具；普通空间继续使用原有编辑器")
        if action in {"nodes", "diagnose", "resources", "group-filters"}:
            if action == "group-filters":
                return group_filter_view(request.get("group"))
            if action == "nodes":
                return {"nodes": node_catalog(native), "statuses": snapshot.get("node_status", {}), "job": snapshot.get("group_status", {}).get("native_nodes", {})}
            if action == "resources":
                from .shared_resources import bindings
                return {"bindings": bindings(self.spaces, scope)}
            result = diagnose(native, request.get("query", "example.com"), request.get("context"))
            if request.get("scan") is True:
                if not self.scan_slot.acquire(blocking=False):
                    raise ConfigError("已有规则缓存检查运行中，请稍后重试")
                try:
                    result["entry_scan"] = scan_rule_entries(native)
                finally:
                    self.scan_slot.release()
            return result
        runtime.workspace._revision(request.get("revision"))
        if action == "group-preview":
            return preview_group(self.root, native, request)
        if action == "repair":
            snapshot["profile"]["native_configuration"] = repair_reference(native, request)
            return {"workspace": self.spaces.save_profile(snapshot["profile"], request["revision"], scope)}
        if action == "test-nodes":
            return self.test_nodes(runtime, native, request)
        if action == "import-preview":
            candidate = prepare_import(native, request, fetch=request.get("fetch") is True)
            if "configuration" not in candidate and "nodes" not in candidate:
                return candidate
            token = secrets.token_urlsafe(24)
            with self.lock:
                self.pending = {k: v for k, v in self.pending.items() if time.time() - v[0] < 300 and v[1] != scope}
                if len(self.pending) >= 2:
                    self.pending.pop(next(iter(self.pending)))
                self.pending[token] = (time.time(), scope, snapshot["revision"], candidate)
            return {k: v for k, v in candidate.items() if k not in {"nodes", "configuration"}} | {"token": token}
        if action == "import-commit":
            with self.lock:
                pending = self.pending.get(request.get("token"))
            if not pending or pending[1] != scope or pending[2] != snapshot["revision"] or time.time() - pending[0] > 300:
                raise ConfigError("导入预览已过期或配置已变化，请重新识别")
            candidate = deepcopy(pending[3])
            if candidate["kind"] == "configuration":
                if request.get("draft") is not True:
                    validate_bundle(compile_bundle({"schema_version": 1, "fallback": "DIRECT", "native_configuration": candidate["configuration"]}, "mihomo", rule_output="remote", component="proxy"), self.root)
                result = {"spaces": self.spaces.import_native(candidate["configuration"], request.get("name"), request.get("catalog_revision"))}
            else:
                if candidate["kind"] == "subscription":
                    snapshot["profile"]["native_configuration"] = candidate["configuration"]
                else:
                    indices = request.get("indices")
                    if not isinstance(indices, list) or not indices or len(set(indices)) != len(indices) or any(type(i) is not int or not 0 <= i < len(candidate["nodes"]) for i in indices):
                        raise ConfigError("请选择有效且不重复的导入项")
                    selected = [candidate["nodes"][i] for i in indices]
                    existing = {p["name"] for p in native["mihomo"].get("proxies", [])}
                    for p in selected:
                        if p["name"] in existing:
                            raise ConfigError("选中项包含同名节点，请取消重复项或先修改名称")
                        existing.add(p["name"])
                    native["mihomo"].setdefault("proxies", []).extend(selected)
                    Profile(snapshot["profile"])
                result = {"workspace": self.spaces.save_profile(snapshot["profile"], request["revision"], scope)}
            with self.lock:
                self.pending.pop(request.get("token"), None)
            return result
        raise ConfigError("工具操作不存在")

    def test_nodes(self, runtime, native, request):
        rows = node_catalog(native, private=True)
        ids = request.get("ids")
        if not isinstance(ids, list) or not ids or len(ids) > 300 or len(set(ids)) != len(ids):
            raise ConfigError("一次请选择 1–300 个不重复节点")
        selected = [n for n in rows if n["id"] in ids]
        if len(selected) != len(ids):
            raise ConfigError("节点列表已改变，请刷新后重试")
        url = request.get("url") or "https://www.gstatic.com/generate_204"
        if not isinstance(url, str) or urlsplit(url).scheme not in {"http", "https"} or not urlsplit(url).hostname or urlsplit(url).username or urlsplit(url).password:
            raise ConfigError("测速 URL 需要完整 HTTP(S) 地址")
        timeout = request.get("timeout", 5000)
        if type(timeout) is not int or not 1000 <= timeout <= 30000:
            raise ConfigError("测速超时需为 1000–30000 毫秒")
        # A global native job key prevents overlapping probes within a space.
        def run():
            runtime.workspace.probe_status("native_nodes", {"test_state": "running", "test_total": len(selected), "test_finished": 0, "error": "", "location": "YoMesh 所在设备"})
            try:
                isolated = deepcopy(native)
                for row in selected:
                    node = deepcopy(row["raw"])
                    node["name"] = "probe_" + row["id"]
                    isolated["mihomo"].setdefault("proxies", []).append(node)
                with native_core(self.root, isolated) as core:
                    with ThreadPoolExecutor(max_workers=4) as pool:
                        futures = {pool.submit(measure_node, core, n, url, timeout): n for n in selected}
                        for i, future in enumerate(as_completed(futures), 1):
                            row = futures[future]
                            with runtime.workspace.lock:
                                record_probe_result(runtime.workspace, row["id"], future.result(), {n["id"] for n in rows})
                                runtime.workspace.probe_status("native_nodes", {"test_finished": i})
                runtime.workspace.probe_status("native_nodes", {"test_state": "success", "tested_at": time.time()})
            except Exception as exc:
                runtime.workspace.probe_status("native_nodes", {"test_state": "error", "error": str(exc) if isinstance(exc, ConfigError) else "原生测速未完成"})
        if not runtime.subscriptions._launch("native_nodes", "test", run):
            raise ConfigError("本空间已有节点测速运行中")
        return {"started": True}
