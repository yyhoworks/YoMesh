"""Use the actual pinned cores, including a short isolated MosDNS startup probe."""
from __future__ import annotations

import json
import hashlib
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path

from .model import ConfigError


def find_core(root: Path, name: str):
    directory = root / "tools" / "cores" / name
    if sys.platform == "win32":
        found = list(directory.rglob("*.exe")) if directory.exists() else []
        found = [p for p in found if p.name == name + ".exe" or (name == "mihomo" and p.name.startswith("mihomo-"))]
    else:
        found = [directory / name] if (directory / name).is_file() else []
    if len(found) != 1:
        raise ConfigError(f"缺少唯一的 {name} 内核；Windows 使用 bootstrap-cores.ps1，Docker 镜像需要包含对应 Linux 内核")
    return found[0]


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def serialize_config(value):
    return value if isinstance(value, (str, bytes)) else json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_bundle_files(directory: Path, files):
    for name, config in files.items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ConfigError("配置包包含无效的文件路径")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(config, bytes):
            path.write_bytes(config)
        else:
            path.write_text(serialize_config(config), encoding="utf-8")


def run_check(command, cwd):
    try:
        result = subprocess.run([str(v) for v in command], cwd=cwd, capture_output=True, timeout=25)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ConfigError("原生校验未完成：" + type(exc).__name__) from None
    if result.returncode:
        # Avoid reflecting core error output containing node passwords to UI/logs.
        raise ConfigError(f"{Path(command[0]).name} 原生校验失败，退出码 {result.returncode}；请在本机终端检查对应配置")
    return {"status": "passed", "method": "native-check", "exit_code": 0}


def check_mosdns(binary, config, cwd):
    config = deepcopy(config)
    config.pop("api", None)
    if isinstance(config.get("log"), dict):
        config["log"].pop("file", None)
    # Every server gets its own loopback endpoint. Reusing one port for multiple
    # TCP (or UDP) servers would make an otherwise valid custom config fail.
    listeners = []
    used_ports = set()
    original_endpoints = set()
    for plugin in config["plugins"]:
        if plugin["type"] in {"udp_server", "tcp_server"}:
            endpoint = (plugin["type"], plugin["args"].get("listen", ""))
            if endpoint in original_endpoints:
                raise ConfigError("MosDNS 同协议监听地址重复，请为各监听设置不同地址或端口")
            original_endpoints.add(endpoint)
            for _ in range(100):
                port = 10000 + secrets.randbelow(39000)
                if port in used_ports:
                    continue
                with socket.socket() as tcp, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    try:
                        tcp.bind(("127.0.0.1", port))
                        udp.bind(("127.0.0.1", port))
                        break
                    except OSError:
                        continue
            else:
                raise ConfigError("无法分配 MosDNS 临时 TCP/UDP 检查端口")
            used_ports.add(port)
            listeners.append((plugin["type"], port))
            plugin["args"]["listen"] = f"127.0.0.1:{port}"
        elif plugin["type"].endswith("_server"):
            raise ConfigError("MosDNS 隔离检查目前支持 UDP / TCP 服务，请将其他监听放在独立配置中")
        if plugin["type"] == "cache" and isinstance(plugin.get("args"), dict):
            plugin["args"].pop("dump_file", None)
    if not listeners:
        raise ConfigError("MosDNS 配置需要 UDP 或 TCP 监听以进行原生校验")
    path = Path(cwd) / "mosdns-smoke.yaml"
    write_json(path, config)
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen([str(binary), "start", "-c", str(path), "-d", str(cwd)], stdout=output, stderr=output, cwd=cwd)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    raise ConfigError("MosDNS 原生启动校验失败：配置插件无法加载")
                try:
                    ready = True
                    for kind, port in listeners:
                        if kind == "tcp_server":
                            with socket.create_connection(("127.0.0.1", port), timeout=.15):
                                pass
                        else:
                            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                                try:
                                    probe.bind(("127.0.0.1", port))
                                    ready = False
                                except OSError:
                                    pass
                    if ready and proc.poll() is None:
                        break
                except OSError:
                    pass
                time.sleep(.05)
            else:
                raise ConfigError("MosDNS 原生启动校验超时")
            return {"status": "passed", "method": "native-startup", "listener": "temporary-loopback-port", "note": "验证插件加载和监听；未探测外部 DNS 或代理连通性"}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)


def validate_bundle(bundle, root: Path):
    proxy = bundle["manifest"]["proxy"]
    component = bundle["manifest"].get("component", "both")
    if component not in {"both", "proxy", "dns"}:
        raise ConfigError("配置包导出范围无效")
    localdns = bundle["manifest"].get("dns_engine") == "localdns"
    dns_name = "localdns" if localdns else "mosdns"
    binary = find_core(root, proxy) if component != "dns" else None
    mosdns = find_core(root, dns_name) if component != "proxy" else None
    if localdns and mosdns:
        from .native_configuration import LOCALDNS_SHA256
        if hashlib.sha256(mosdns.read_bytes()).hexdigest() != LOCALDNS_SHA256:
            raise ConfigError("LocalDNS 程序哈希与已核对的 FJGAC 版本不符")
    versions = bundle["manifest"]["versions"]
    targets = []
    if binary:
        targets.append((proxy, binary, ["-v"] if proxy == "mihomo" else ["version"]))
    if mosdns:
        targets.append((dns_name, mosdns, ["--version"] if localdns else ["version"]))
    for name, executable, flags in targets:
        try:
            info = subprocess.run([str(executable), *flags], capture_output=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            raise ConfigError(f"{name} 无法读取版本") from None
        if info.returncode or versions[name] not in (info.stdout + info.stderr).decode("utf-8", errors="replace"):
            raise ConfigError(f"{name} 版本与锁定版本 {versions[name]} 不符，请先更新兼容性测试与版本锁")
    with tempfile.TemporaryDirectory(prefix="yomesh-check-") as scratch:
        directory = Path(scratch)
        filename = proxy + (".yaml" if proxy == "mihomo" else ".json")
        file = directory / filename
        write_bundle_files(directory, bundle["files"])
        result = {"status": "passed"}
        if binary:
            command = [binary, "-t", "-d", directory, "-f", file] if proxy == "mihomo" else [binary, "check", "-c", file]
            result[proxy] = run_check(command, directory)
        if mosdns:
            result[dns_name] = run_check([mosdns, "--root", directory / "localdns", "--config", directory / "localdns/config/localdns.json", "--check"], directory) if localdns else check_mosdns(mosdns, bundle["files"]["mosdns.yaml"], directory)
    bundle["manifest"]["validation"] = result
    return result
