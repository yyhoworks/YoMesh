"""Subscription jobs, scheduling and real Mihomo HTTP latency measurements."""
from __future__ import annotations

import json
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .compiler import node_mihomo
from .model import ConfigError
from .native import find_core, write_json
from .subscriptions import fetch_subscription
from .workspace import next_update


class SubscriptionService:
    def __init__(self, root, workspace, fetcher=fetch_subscription, rule_library=None):
        self.root, self.workspace, self.fetcher = Path(root), workspace, fetcher
        self.running = set()
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.scheduler = None
        self.rule_library = rule_library

    def start_scheduler(self):
        self.scheduler = threading.Thread(target=self._schedule, daemon=True, name="subscription-updates")
        self.scheduler.start()

    def _schedule(self):
        while not self.stop.wait(15):
            self._run_scheduled_updates()

    def _run_scheduled_updates(self):
        due = self.workspace.due_updates()
        if self.rule_library:
            for ident in due["rules"]:
                try:
                    self.rule_library.refresh(ident)
                except ConfigError:
                    pass
        for ident in due["sources"]:
            try:
                self.refresh(ident)
            except ConfigError:
                pass

    def _launch(self, ident, kind, action):
        key = (ident, kind)
        with self.lock:
            if key in self.running:
                return False
            if len(self.running) >= 4:
                raise ConfigError("已有 4 项订阅任务运行中，请稍后再试")
            self.running.add(key)
        def run():
            try:
                action()
            finally:
                with self.lock:
                    self.running.discard(key)
        threading.Thread(target=run, daemon=True, name="source-" + kind).start()
        return True

    def busy(self, ident):
        with self.lock:
            return any(key[0] == ident for key in self.running)

    def refresh(self, ident):
        with self.workspace.lock:
            source = deepcopy(self.workspace.source(ident))
        if not source.get("enabled", True):
            raise ConfigError("订阅已停用，请先启用再刷新")
        def action():
            self.workspace.status(ident, {"refresh_state": "running", "last_attempt": time.time(), "error": ""})
            try:
                nodes, stats = self.fetcher(source)
                self.workspace.replace_nodes(source, nodes, stats)
            except Exception as error:
                message = str(error) if isinstance(error, ConfigError) else "订阅拉取或解析失败，原节点已保留"
                self.workspace.status(ident, {"refresh_state": "error", "error": message, "next_update": time.time() + 300 if source.get("auto_update") else None})
        return self._launch(ident, "refresh", action)

    def test(self, ident):
        snapshot = self.workspace.snapshot()
        source = next((s for s in snapshot["profile"]["sources"] if s["id"] == ident), None)
        if not source or not source.get("enabled", True):
            raise ConfigError("订阅不存在或已停用")
        nodes = [n for n in snapshot["profile"]["nodes"] if n.get("source") == ident and n.get("enabled", True)]
        if not nodes:
            raise ConfigError("暂无可测速节点，请先刷新订阅")
        def action():
            self.workspace.status(ident, {"test_state": "running", "test_finished": 0, "test_total": len(nodes), "test_error": ""})
            try:
                self._measure(source, nodes)
            except Exception as error:
                message = str(error) if isinstance(error, ConfigError) else "测速内核启动失败，请稍后重试"
                self.workspace.status(ident, {"test_state": "error", "test_error": message})
        return self._launch(ident, "test", action)

    def test_group(self, ident):
        from .model import Profile
        snapshot = self.workspace.snapshot()
        profile = snapshot["profile"]
        group = next((g for g in profile["groups"] if g["id"] == ident), None)
        if not group or not group.get("enabled", True):
            raise ConfigError("节点组不存在或已停用")
        validated = Profile({"schema_version": 1, "sources": profile["sources"], "nodes": profile["nodes"], "fallback": "DIRECT"})
        validated.index["groups"] = {g["id"]: g for g in profile["groups"]}
        for g in profile["groups"]:
            validated._group(g)
        found = set()
        def collect(group_id):
            for member in validated.members(group_id, allow_empty=True):
                if member in validated.index["nodes"]:
                    found.add(member)
                else:
                    collect(member)
        collect(ident)
        nodes = [n for n in validated.data["nodes"] if n["id"] in found]
        if not nodes:
            raise ConfigError("节点组没有可测速的成员")
        options = {"test_url": group.get("test_url", "https://www.gstatic.com/generate_204"), "test_timeout_ms": group.get("test_timeout_ms", 3000)}
        def action():
            self.workspace.probe_status(ident, {"test_state": "running", "test_finished": 0, "test_total": len(nodes)})
            try:
                successful = self._measure(options, nodes, ident)
                self.workspace.probe_status(ident, {"test_state": "success", "tested_at": time.time(), "test_success": successful, "test_total": len(nodes)})
            except Exception as error:
                self.workspace.probe_status(ident, {"test_state": "error", "error": str(error) if isinstance(error, ConfigError) else "节点组测速失败"})
        return self._launch("group_" + ident, "test", action)

    def test_nodes(self, ids):
        snapshot = self.workspace.snapshot()
        profile = snapshot["profile"]
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
            raise ConfigError("请选择不重复的节点 ID")
        sources = {s["id"]: s for s in profile["sources"]}
        nodes = [n for n in profile["nodes"] if n["id"] in ids]
        if len(nodes) != len(ids) or any(not n.get("enabled", True) or not sources.get(n.get("source"), {}).get("enabled", True) for n in nodes):
            raise ConfigError("节点不存在或已停用，请刷新后重新选择")
        ident = "node_selection"
        options = {"test_url": "https://www.gstatic.com/generate_204", "test_timeout_ms": 3000}
        def action():
            self.workspace.probe_status(ident, {"test_state": "running", "test_finished": 0, "test_total": len(nodes), "error": ""})
            try:
                successful = self._measure(options, nodes, ident)
                self.workspace.probe_status(ident, {"test_state": "success", "tested_at": time.time(), "test_success": successful, "test_total": len(nodes)})
            except Exception as error:
                self.workspace.probe_status(ident, {"test_state": "error", "error": str(error) if isinstance(error, ConfigError) else "节点测速失败"})
        return self._launch(ident, "test", action)

    def _measure(self, source, nodes, group_id=None):
        binary = find_core(self.root, "mihomo")
        with socket.socket() as holder:
            holder.bind(("127.0.0.1", 0))
            port = holder.getsockname()[1]
        secret = secrets.token_urlsafe(24)
        config = {"mixed-port": 0, "allow-lan": False, "bind-address": "127.0.0.1", "external-controller": f"127.0.0.1:{port}", "secret": secret, "mode": "rule", "log-level": "silent", "proxies": [node_mihomo(n, "probe_" + n["id"]) for n in nodes], "rules": ["MATCH,DIRECT"], "dns": {"enable": True, "nameserver": ["223.5.5.5"], "default-nameserver": ["223.5.5.5"], "proxy-server-nameserver": ["223.5.5.5"]}}
        with tempfile.TemporaryDirectory(prefix="yomesh-latency-") as temporary, tempfile.TemporaryFile() as logs:
            path = Path(temporary)
            write_json(path / "probe.json", config)
            proc = subprocess.Popen([str(binary), "-d", str(path), "-f", str(path / "probe.json")], cwd=path, stdout=logs, stderr=logs)
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        raise ConfigError("测速内核无法加载订阅节点；请查看跳过项目或节点参数")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=.1):
                            break
                    except OSError:
                        time.sleep(.05)
                else:
                    raise ConfigError("测速内核启动超时")
                def measure(node):
                    query = urlencode({"url": source["test_url"], "timeout": source["test_timeout_ms"]})
                    request = Request(f"http://127.0.0.1:{port}/proxies/" + quote("probe_" + node["id"], safe="") + "/delay?" + query, headers={"Authorization": "Bearer " + secret})
                    try:
                        with urlopen(request, timeout=source["test_timeout_ms"] / 1000 + 3) as reply:
                            delay = json.load(reply).get("delay")
                        if not isinstance(delay, int) or delay < 0:
                            raise ValueError()
                        return {"state": "success", "latency_ms": delay}
                    except Exception:
                        return {"state": "failed", "latency_ms": None, "error": "超时或连接失败"}
                successful = 0
                with ThreadPoolExecutor(max_workers=4) as pool:
                    futures = {pool.submit(measure, node): node for node in nodes}
                    for finished, future in enumerate(as_completed(futures), 1):
                        result = future.result()
                        successful += result["state"] == "success"
                        if group_id:
                            with self.workspace.lock:
                                self.workspace.state["node_status"][futures[future]["id"]] = {**result, "tested_at": time.time()}
                                self.workspace.probe_status(group_id, {"test_finished": finished})
                        else:
                            self.workspace.node_result(source["id"], futures[future]["id"], result, finished, len(nodes))
                if not group_id:
                    self.workspace.status(source["id"], {"test_state": "success", "tested_at": time.time(), "test_success": successful, "test_failed": len(nodes) - successful})
                return successful
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
