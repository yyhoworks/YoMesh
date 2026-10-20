"""Revocable subscription links, compiled from the same current Profile."""
from __future__ import annotations

import hashlib
from http.client import HTTPException
import json
import os
import re
import secrets
import tempfile
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import yaml

from .compiler import compile_bundle
from .model import ConfigError
from .native import serialize_config, validate_bundle
from .native_configuration import rule_yaml_entries, safe_path


def public_base(value):
    if not isinstance(value, str) or len(value) > 2048 or any(c.isspace() for c in value):
        raise ConfigError("请填写完整的 HTTP / HTTPS 对外访问地址")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ConfigError("对外访问地址的主机或端口无效") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ConfigError("对外访问地址只填写协议、主机和端口，不包含路径、凭据或查询参数")
    return parsed.scheme + "://" + parsed.netloc


def atomic_write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def as_bytes(value):
    result = serialize_config(value)
    return result if isinstance(result, bytes) else result.encode("utf-8")


class Publications:
    def __init__(self, root, spaces):
        self.root, self.spaces = Path(root), spaces
        self.file = self.root / "data/published-links.json"
        self.lock = threading.RLock()
        self.state = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {"links": []}
        self.cache = OrderedDict()
        self.checked_at = OrderedDict()
        self.refresh_limit = threading.BoundedSemaphore(2)
        self.refreshing = set()

    def _write(self):
        atomic_write(self.file, json.dumps(self.state, ensure_ascii=False).encode())

    @staticmethod
    def _main(record):
        return record["proxy"] + (".yaml" if record["proxy"] == "mihomo" else ".json")

    def _view(self, record):
        return {key: record[key] for key in ("id", "proxy", "rule_address", "base_url")} | {"url": record["base_url"] + "/s/" + record["token"] + "/" + self._main(record)}

    def listing(self, space):
        with self.lock:
            space = space or self.spaces.state["active"]
            self.spaces.runtime(space)
            return {"links": [self._view(row) for row in self.state["links"] if row["space"] == space]}

    def create(self, space, request):
        base = public_base(request.get("base_url"))
        proxy, address = request.get("proxy", "mihomo"), request.get("rule_address", "original")
        if proxy not in {"mihomo", "sing-box"} or address not in {"original", "site"}:
            raise ConfigError("请选择代理内核及原始地址 / 本站地址")
        with self.lock:
            space = space or self.spaces.state["active"]
            snapshot = self.spaces.workspace_snapshot(space)
            if snapshot["revision"] != request.get("revision"):
                from .workspace import Conflict
                raise Conflict("配置已更新，请刷新后重新生成链接")
            existing = next((row for row in self.state["links"] if row["space"] == space and row["proxy"] == proxy and row["rule_address"] == address), None)
            record = deepcopy(existing) if existing else {"id": "link_" + secrets.token_hex(10), "token": secrets.token_urlsafe(32), "space": space, "proxy": proxy, "rule_address": address}
            record["base_url"] = base
            # Validate a concrete result before exposing a persistent URL.
            built = self._build(record, snapshot["profile"])
            if existing:
                self.state["links"].remove(existing)
            self.state["links"].append(record)
            self._write()
            self.cache.pop(record["token"], None)
            return {**self._view(record), "validation": "passed", "rule_count": built["rule_count"]}

    def revoke(self, space, ident):
        with self.lock:
            space = space or self.spaces.state["active"]
            record = next((row for row in self.state["links"] if row["space"] == space and row["id"] == ident), None)
            if not record:
                raise ConfigError("订阅链接不存在或已停用")
            self.state["links"].remove(record)
            self.cache.pop(record["token"], None)
            self._write()
            return self.listing(space)

    def _build(self, record, profile):
        proxy = record["proxy"]
        mode = "local" if record["rule_address"] == "site" and not profile.get("native_configuration") else "remote"
        bundle = compile_bundle(profile, proxy, rule_output=mode, component="proxy")
        filename = self._main(record)
        current = bundle["files"][filename]
        config = yaml.safe_load(current) if isinstance(current, str) else deepcopy(current)
        resources = {}
        native = profile.get("native_configuration", {})
        adapters = native.get("rule_source_adapters", {})

        def resource(section, name, definition, content, path=None, adapter=False):
            ident = hashlib.sha256((section + ":" + name).encode()).hexdigest()[:24]
            local = path or "rules/published_" + ident + ".yaml"
            local = safe_path(local.removeprefix("./"))
            raw = as_bytes(content)
            resources[ident] = {"body": raw, "definition": deepcopy(definition), "section": section, "path": local, "adapter": adapter}
            bundle["files"][local] = raw
            return record["base_url"] + "/s/" + record["token"] + "/assets/" + ident, local

        if proxy == "mihomo":
            for section in ("rule-providers", "proxy-providers"):
                for name, definition in config.get(section, {}).items():
                    own_rule = section == "rule-providers" and record["rule_address"] == "site"
                    if not own_rule and definition.get("type") != "file":
                        continue
                    original = deepcopy(definition)
                    path = definition.get("path", "").removeprefix("./")
                    if definition.get("type") == "inline":
                        content = yaml.safe_dump({"payload": definition["payload"]}, allow_unicode=True, sort_keys=False)
                    elif path in bundle["files"]:
                        content = bundle["files"][path]
                    else:
                        raise ConfigError("生成订阅链接需要先刷新对应规则或节点来源的缓存")
                    url, local = resource(section, name, original, content, path or None, name in adapters)
                    definition.update(type="http", url=url, path="./" + local)
                    definition.setdefault("interval", 86400)
                    definition.pop("payload", None)
                    # Upstream-specific credentials must never be sent to this server.
                    for field in ("header", "proxy"):
                        definition.pop(field, None)
            # A single-file client subscription cannot rely on package-only PEM paths.
            for listener in config.get("listeners", []):
                for field in ("certificate", "private-key"):
                    path = listener.get(field)
                    if isinstance(path, str) and path and "\n" not in path:
                        content = bundle["files"].get(path.removeprefix("./"))
                        if content is None:
                            raise ConfigError("订阅链接缺少入站证书资产；请使用完整配置包")
                        text = as_bytes(content).decode("utf-8")
                        if not text.lstrip().startswith("-----BEGIN "):
                            raise ConfigError("订阅链接只支持内嵌 PEM 证书；其他资产请使用完整配置包")
                        listener[field] = text
            bundle["files"][filename] = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
            rule_count = len(config.get("rule-providers", {}))
        else:
            for definition in config.get("route", {}).get("rule_set", []):
                if record["rule_address"] != "site" and definition.get("type") != "local":
                    continue
                path = definition.get("path", "").removeprefix("./")
                if path not in bundle["files"]:
                    raise ConfigError("本站规则地址需要本地规则快照；请先使用本地规则输出方案")
                url, _ = resource("rule_set", definition["tag"], definition, bundle["files"][path], path)
                definition.update(type="remote", url=url)
                definition.pop("path", None)
                definition.pop("http_client", None)
            bundle["files"][filename] = config
            rule_count = len(config.get("route", {}).get("rule_set", []))
        validate_bundle(bundle, self.root)
        return {"bundle": bundle, "main": as_bytes(bundle["files"][filename]), "resources": resources, "rule_count": rule_count}

    def read(self, token, asset=None, filename=None):
        with self.lock:
            record = next((row for row in self.state["links"] if secrets.compare_digest(row["token"], token)), None)
            if record is None or (filename is not None and filename != self._main(record)):
                return None
            try:
                profile = self.spaces.workspace_snapshot(record["space"])["profile"]
            except ConfigError:
                return None
            fingerprint = hashlib.sha256(json.dumps([record, profile], ensure_ascii=False, sort_keys=True).encode()).digest()
            cached = self.cache.get(token)
            if not cached or cached[0] != fingerprint:
                built = self._build(record, profile)
                self.cache[token] = (fingerprint, built)
                while len(self.cache) > 2:
                    self.cache.popitem(last=False)
            else:
                built = cached[1]
                self.cache.move_to_end(token)
            if asset is None:
                return built["main"], "text/yaml; charset=utf-8" if record["proxy"] == "mihomo" else "application/json; charset=utf-8", "profile"
            item = built["resources"].get(asset)
            if item is None:
                return None
        return self._resource(record, asset, item, built)

    def _resource(self, record, ident, item, built):
        cache_file = self.root / "data/published-cache" / record["token"] / ident
        key = (record["token"], ident)
        definition = item["definition"]
        is_rule = item["section"] == "rule-providers"
        current = item["body"]
        # Resources follow the current Profile; cached downloads are scoped to its source.
        identity = hashlib.sha256(json.dumps(definition, sort_keys=True).encode() + current).digest()
        last = self.checked_at.get(key)
        if is_rule and definition.get("url") and (last is None or last[0] != identity or time.monotonic() - last[1] > 300):
            with self.lock:
                acquired = key not in self.refreshing and self.refresh_limit.acquire(blocking=False)
                if acquired:
                    self.refreshing.add(key)
            if not acquired:
                fallback_source = "snapshot"
                if cache_file.exists():
                    stored_identity, _, stored = cache_file.read_bytes().partition(b"\n")
                    if stored_identity == identity.hex().encode():
                        current = stored
                        fallback_source = "cache"
                return current, "application/octet-stream" if definition.get("format") == "mrs" else "text/plain; charset=utf-8", fallback_source
            source = "snapshot"
            try:
                headers = {name: value[0] if isinstance(value, list) else value for name, value in definition.get("header", {}).items()}
                with urlopen(Request(definition["url"], headers=headers), timeout=4) as response:
                    raw = response.read(8 * 1024 * 1024 + 1)
                if not raw or len(raw) > 8 * 1024 * 1024:
                    raise ConfigError("远程规则内容为空或过大")
                if item["adapter"]:
                    from .native_rule_sources import normalize_rule_list
                    raw = normalize_rule_list(raw.decode("utf-8-sig"))[0].encode()
                fmt = definition.get("format", "yaml")
                if fmt == "yaml":
                    entries = rule_yaml_entries(raw.decode("utf-8-sig"))
                    refs = {ref for line in entries for ref in re.findall(r"RULE-SET,([^,)]+)", line)}
                    main = yaml.safe_load(built["bundle"]["files"]["mihomo.yaml"])
                    if refs - set(main.get("rule-providers", {})):
                        raise ConfigError("更新规则包含当前配置没有的依赖")
                elif fmt == "mrs":
                    from .rule_sources import parse_rules
                    parse_rules(raw, "mrs", self.root)
                elif fmt not in {"mrs", "text"}:
                    raise ConfigError("不支持的远程规则格式")
                candidate = deepcopy(built["bundle"])
                candidate["files"][item["path"]] = raw
                validate_bundle(candidate, self.root)
                atomic_write(cache_file, identity.hex().encode() + b"\n" + raw)
                current, source = raw, "upstream"
            except (OSError, ValueError, UnicodeError, ConfigError, yaml.YAMLError, HTTPException):
                if cache_file.exists():
                    stored_identity, _, stored = cache_file.read_bytes().partition(b"\n")
                    if stored_identity == identity.hex().encode():
                        current, source = stored, "cache"
            finally:
                with self.lock:
                    self.refreshing.discard(key)
                    self.refresh_limit.release()
            with self.lock:
                self.checked_at[key] = (identity, time.monotonic(), source)
                self.checked_at.move_to_end(key)
                while len(self.checked_at) > 128:
                    self.checked_at.popitem(last=False)
        elif last and last[0] == identity:
            source = last[2]
            if cache_file.exists():
                stored_identity, _, stored = cache_file.read_bytes().partition(b"\n")
                if stored_identity == identity.hex().encode():
                    current = stored
        else:
            source = "snapshot"
        kind = "application/octet-stream" if definition.get("format") in {"mrs", "binary"} else "text/plain; charset=utf-8"
        return current, kind, source
