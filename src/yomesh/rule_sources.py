"""Fetch and normalize rule files without guessing away unsupported semantics."""
import base64
import ipaddress
import json
import subprocess
import tempfile
import threading
import time
import uuid
from copy import deepcopy
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

from .model import ConfigError, Profile
from .native import find_core
from .workspace import next_update

MAX_BYTES = 16 * 1024 * 1024
FORMATS = {"auto", "domain", "ipcidr", "classical", "clash", "surge", "adguard", "sing-box", "mrs", "srs"}


def clean_source(value):
    if not isinstance(value, dict):
        raise ConfigError("规则来源需要对象")
    source = {"type": value.get("type", "manual"), "format": value.get("format", "auto")}
    if source["type"] not in {"manual", "url", "file"} or source["format"] not in FORMATS:
        raise ConfigError("规则来源或格式无效")
    if source["type"] == "file":
        source["filename"] = Path(str(value.get("filename", "rules"))).name[:200]
    if source["type"] != "url":
        return source
    address = value.get("url", "")
    if not isinstance(address, str) or len(address) > 8192:
        raise ConfigError("规则订阅地址无效")
    parsed = urlsplit(address)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ConfigError("规则订阅需要 HTTP / HTTPS 地址")
    source.update(url=address, auto_update=value.get("auto_update", True), interval_minutes=value.get("interval_minutes", 1440), request_timeout=value.get("request_timeout", 20), user_agent=value.get("user_agent", "YoMesh/0.5"), schedule_mode=value.get("schedule_mode", "interval"), daily_time=value.get("daily_time", "04:00"))
    if not isinstance(source["auto_update"], bool) or type(source["interval_minutes"]) is not int or not 1 <= source["interval_minutes"] <= 43200:
        raise ConfigError("规则更新间隔需要 1–43200 分钟")
    if type(source["request_timeout"]) is not int or not 3 <= source["request_timeout"] <= 120:
        raise ConfigError("请求超时需要 3–120 秒")
    if not isinstance(source["user_agent"], str) or len(source["user_agent"]) > 500 or any(c in source["user_agent"] for c in "\r\n"):
        raise ConfigError("规则请求 UA 无效")
    if source["schedule_mode"] not in {"interval", "daily"}:
        raise ConfigError("规则更新计划无效")
    try:
        time.strptime(source["daily_time"], "%H:%M")
    except (ValueError, TypeError):
        raise ConfigError("每天时刻需要 HH:MM") from None
    return source


def due_time(source):
    if source["type"] != "url" or not source.get("auto_update"):
        return None
    if source.get("schedule_mode") == "daily":
        return next_update(source)
    return time.time() + source["interval_minutes"] * 60


def fetch(source):
    try:
        request = Request(source["url"], headers={"User-Agent": source["user_agent"]})
        with urlopen(request, timeout=source["request_timeout"]) as response:
            if urlsplit(response.url).scheme not in {"http", "https"}:
                raise ConfigError("规则订阅重定向需要 HTTP / HTTPS")
            body = response.read(MAX_BYTES + 1)
        if len(body) > MAX_BYTES:
            raise ConfigError("规则文件超过 16 MiB")
        return body
    except ConfigError:
        raise
    except HTTPError as error:
        raise ConfigError(f"规则订阅返回 HTTP {error.code}，原内容保留") from None
    except (URLError, OSError, ValueError):
        raise ConfigError("规则订阅连接失败，请检查地址和超时；原内容保留") from None


def singbox_entries(value):
    names = {"domain": "DOMAIN", "domain_suffix": "DOMAIN-SUFFIX", "domain_keyword": "DOMAIN-KEYWORD", "domain_regex": "DOMAIN-REGEX", "ip_cidr": "IP-CIDR"}
    if not isinstance(value, dict) or set(value) - {"version", "rules"} or value.get("version") not in {1, 2, 3, 4} or not isinstance(value.get("rules"), list):
        raise ConfigError("需要 sing-box rule-set JSON（version / rules）")

    def convert(rule):
        if not isinstance(rule, dict):
            raise ConfigError("sing-box 规则需要对象")
        if rule.get("type") == "logical":
            if set(rule) - {"type", "mode", "rules"} or rule.get("mode") != "or" or not isinstance(rule.get("rules"), list):
                raise ConfigError("规则文件包含 AND / 取反逻辑，请在分流规则的条件组中编排；未导入")
            return [entry for child in rule["rules"] for entry in convert(child)]
        keys = set(rule) - {"type"}
        domain_keys = {"domain", "domain_suffix", "domain_keyword", "domain_regex"}
        # sing-box 1.14.3 combines the domain matcher family with OR. Other
        # families remain AND; never flatten a domain + IP combination.
        if not keys or not keys.issubset(names) or (len(keys) > 1 and not keys.issubset(domain_keys)) or rule.get("type", "default") != "default":
            raise ConfigError("规则文件含未支持的字段或多字段 AND；不能作为普通规则集展开")
        entries = []
        for key in rule:
            if key == "type":
                continue
            values = rule[key]
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
                raise ConfigError("sing-box 规则内容需要文本数组")
            entries.extend(("IP-CIDR6" if key == "ip_cidr" and ":" in item else names[key]) + "," + item for item in values)
        return entries

    return [entry for rule in value["rules"] for entry in convert(rule)]


def parse_rules(body, format_name, root):
    if format_name not in FORMATS or not body or len(body) > MAX_BYTES:
        raise ConfigError("规则文件为空、格式无效或超过 16 MiB")
    detected = format_name
    if detected == "auto":
        detected = "mrs" if body[:3] == b"MRS" or body[:4] == b"\x28\xb5\x2f\xfd" else "srs" if body[:3] == b"SRS" else "auto"
    if detected in {"mrs", "srs"}:
        with tempfile.TemporaryDirectory(prefix="yomesh-rules-") as temporary:
            directory = Path(temporary)
            source, target = directory / ("rules." + detected), directory / "decoded.txt"
            source.write_bytes(body)
            if detected == "srs":
                result = subprocess.run([str(find_core(root, "sing-box")), "rule-set", "decompile", str(source), "-o", str(target)], capture_output=True, timeout=25)
                if result.returncode or not target.exists():
                    raise ConfigError("SRS 规则解码失败，文件未导入")
                if target.stat().st_size > MAX_BYTES:
                    raise ConfigError("SRS 解码内容超过 16 MiB")
                return parse_rules(target.read_bytes(), "sing-box", root)[0], "srs"
            for behavior in ("domain", "ipcidr"):
                result = subprocess.run([str(find_core(root, "mihomo")), "convert-ruleset", behavior, "mrs", str(source), str(target)], capture_output=True, timeout=25)
                if result.returncode == 0 and target.exists():
                    if target.stat().st_size > MAX_BYTES:
                        raise ConfigError("MRS 解码内容超过 16 MiB")
                    return parse_rules(target.read_bytes(), behavior, root)[0], "mrs"
            raise ConfigError("MRS 规则解码失败；支持 domain / ipcidr 规则集")
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise ConfigError("文本规则需要 UTF-8；二进制请选择 MRS 或 SRS") from None
    if detected == "auto":
        stripped = text.lstrip()
        if stripped.startswith("{"):
            try:
                document = json.loads(text)
            except json.JSONDecodeError:
                raise ConfigError("规则 JSON 无效") from None
            detected = "clash" if isinstance(document, dict) and "payload" in document else "sing-box"
        else:
            detected = "clash" if stripped.startswith(("payload:", "---")) or "\npayload:" in text else "classical"
    if detected == "sing-box":
        try:
            entries = singbox_entries(json.loads(text))
        except (json.JSONDecodeError, RecursionError):
            raise ConfigError("sing-box 规则 JSON 无效或嵌套过深") from None
    else:
        if detected == "clash":
            try:
                value = yaml.safe_load(text)
            except yaml.YAMLError:
                raise ConfigError("Clash 规则 YAML 无效") from None
            if not isinstance(value, dict) or set(value) != {"payload"} or not isinstance(value["payload"], list) or not all(isinstance(v, str) for v in value["payload"]):
                raise ConfigError("需要仅包含 payload 列表的 Clash 规则集")
            lines = value["payload"]
        else:
            lines = text.splitlines()
        entries = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith(("#", "!", ";", "[Adblock")):
                continue
            if line.startswith("||") and line.endswith("^") and not any(c in line[2:-1] for c in "*$|/"):
                entries.append("DOMAIN-SUFFIX," + line[2:-1]); continue
            if "," not in line and (line.startswith("@@") or any(c in line for c in ("##", "#@#", "$")) or line.startswith("|")):
                raise ConfigError("包含 AdGuard 例外、元素过滤或修饰符；不能等价转换，未导入")
            if "," in line:
                pieces = line.split(",")
                if len(pieces) == 3 and pieces[2] == "no-resolve" and pieces[0].startswith("IP-CIDR"):
                    line = ",".join(pieces[:2])
                elif len(pieces) != 2:
                    raise ConfigError("规则集只维护匹配内容，不能带代理出口或复杂逻辑")
                entries.append(line)
            else:
                try:
                    network = ipaddress.ip_network(line, strict=False)
                    entries.append(("IP-CIDR6," if network.version == 6 else "IP-CIDR,") + str(network))
                except ValueError:
                    entries.append("DOMAIN-SUFFIX," + line[2:] if line.startswith("+.") else "DOMAIN-SUFFIX," + line[1:] if line.startswith(".") else "DOMAIN," + line)
    entries = list(dict.fromkeys(entries))
    if not entries or len(entries) > 250000:
        raise ConfigError("规则内容需要 1–250000 条")
    item = {"id": "validation", "name": "validation", "kind": "classical", "entries": entries}
    Profile({"schema_version": 1, "library": [item], "fallback": "DIRECT"})
    return item, detected


class RuleLibraryService:
    def __init__(self, root, workspace):
        self.root, self.workspace = Path(root), workspace
        self.running, self.lock = set(), threading.Lock()

    def prepare(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("name"), str) or not value["name"].strip() or len(value["name"]) > 100:
            raise ConfigError("规则集需要 1–100 字符名称")
        source = clean_source(value.get("source", {}))
        if "matchers" in value:
            from .rulelogic import condition_expression
            if source["type"] != "manual" or not isinstance(value["matchers"], list) or not 1 <= len(value["matchers"]) <= 64:
                raise ConfigError("规则匹配器需要 1–64 个本地匹配条件")
            entries = []
            for matcher in value["matchers"]:
                parsed = condition_expression(matcher, {}, "规则匹配器")
                for entry in parsed["values"]:
                    if "\n" in entry or "\r" in entry:
                        raise ConfigError("每个匹配值不能含换行")
                    entries.append(parsed["type"] + "," + entry)
            body = "\n".join(entries).encode("utf-8")
            source["format"] = "classical"
        elif source["type"] == "url":
            body = fetch(source)
        elif "file_base64" in value:
            try:
                body = base64.b64decode(value["file_base64"], validate=True)
            except (ValueError, TypeError):
                raise ConfigError("上传文件内容无效") from None
        else:
            if not isinstance(value.get("text"), str):
                raise ConfigError("请输入规则内容或选择文件")
            body = value["text"].encode()
        try:
            item, detected = parse_rules(body, source["format"], self.root)
        except subprocess.TimeoutExpired:
            raise ConfigError("规则转换超时，原内容保留；请检查文件格式或大小") from None
        item.update(id=value.get("id") or "l_" + uuid.uuid4().hex[:12], name=value["name"].strip(), source=source, note=str(value.get("note", ""))[:500])
        return item, {"refresh_state": "success", "detected_format": detected, "last_updated": time.time(), "last_attempt": time.time(), "next_update": due_time(source), "error": ""}

    def refresh(self, ident):
        with self.workspace.lock:
            item = deepcopy(next((l for l in self.workspace.state["profile"]["library"] if l["id"] == ident), None))
        if not item or item.get("source", {}).get("type") != "url":
            raise ConfigError("只有规则订阅可以刷新")
        with self.lock:
            if ident in self.running:
                return
            if len(self.running) >= 4:
                raise ConfigError("已有 4 个规则更新任务，请稍后")
            self.running.add(ident)

        def run():
            self.workspace.library_status(ident, {"refresh_state": "running", "last_attempt": time.time(), "error": ""})
            try:
                prepared, status = self.prepare({**item})
                self.workspace.replace_library(prepared, status, item)
            except Exception as error:
                self.workspace.library_status(ident, {"refresh_state": "error", "error": str(error) if isinstance(error, ConfigError) else "规则更新失败，原内容保留", "next_update": time.time() + 300 if item["source"].get("auto_update") else None})
            finally:
                with self.lock:
                    self.running.discard(ident)
        threading.Thread(target=run, daemon=True, name="rule-update").start()

    def run_due(self, snapshot):
        for item in snapshot["profile"]["library"]:
            due = snapshot.get("library_status", {}).get(item["id"], {}).get("next_update")
            if due and due <= time.time() and item.get("source", {}).get("auto_update"):
                self.refresh(item["id"])
