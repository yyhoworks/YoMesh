"""Import existing Mihomo, sing-box, LocalDNS, or YoMesh configurations into a space."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import ipaddress
import json
import re
import zipfile
from copy import deepcopy
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import yaml

from .model import BUILTINS, MATCHES, MODES, ConfigError, Profile
from .native_configuration import checked as checked_native, json_document, safe_path
from .rule_sources import clean_source as clean_rule_source, parse_rules
from .subscriptions import SUPPORTED, clash_node, normalize, singbox_node
from .workspace import clean_source, empty_profile

MAX_ARCHIVE_FILES = 512
MAX_TOTAL_BYTES = 16 * 1024 * 1024


def _decode_payload(raw_base64: str | None, raw_text: str | None, label: str = "配置文件") -> bytes:
    if isinstance(raw_base64, str) and raw_base64.strip():
        try:
            data = base64.b64decode(raw_base64, validate=True)
        except (ValueError, binascii.Error):
            raise ConfigError(f"{label} Base64 内容无效") from None
    elif isinstance(raw_text, str) and raw_text.strip():
        data = raw_text.encode("utf-8")
    else:
        raise ConfigError(f"请选择或粘贴要导入的{label}")
    if not data or len(data) > MAX_TOTAL_BYTES:
        raise ConfigError(f"{label}大小需要为 1 字节至 16 MiB")
    return data


def _clean_archive_path(name: str) -> str | None:
    normalized = name.replace("\\", "/").lstrip("/")
    if not normalized or normalized.endswith("/"):
        return None
    parts = PurePosixPath(normalized).parts
    if not parts or any(p in {"", ".", "..", "__MACOSX"} or p.startswith("._") or p == ".DS_Store" for p in parts):
        return None
    return "/".join(parts)


def _unpack_files(raw: bytes, filename: str = "") -> dict[str, bytes]:
    if raw[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                files: dict[str, bytes] = {}
                total = 0
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    cleaned = _clean_archive_path(info.filename)
                    if not cleaned:
                        continue
                    if len(files) >= MAX_ARCHIVE_FILES:
                        raise ConfigError("压缩包内文件数量超过 512 个限制")
                    if info.file_size > MAX_TOTAL_BYTES or total + info.file_size > MAX_TOTAL_BYTES:
                        raise ConfigError("压缩包解压后总大小超过 16 MiB 限制")
                    body = archive.read(info.filename)
                    total += len(body)
                    if total > MAX_TOTAL_BYTES:
                        raise ConfigError("压缩包解压后总大小超过 16 MiB 限制")
                    files[cleaned] = body
        except zipfile.BadZipFile:
            raise ConfigError("压缩包格式损坏，无法读取") from None
        if not files:
            raise ConfigError("压缩包内没有可用的配置文件")
        first_segments = {p.split("/", 1)[0] for p in files if "/" in p}
        if (
            len(first_segments) == 1
            and all("/" in p for p in files)
            and next(iter(first_segments)) not in {"localdns", "config", "rules", "ruleset", "providers", "generated"}
        ):
            prefix = next(iter(first_segments)) + "/"
            files = {p[len(prefix):]: body for p, body in files.items() if p[len(prefix):]}
        return files
    clean_name = _clean_archive_path(Path(filename or "config").name) or "config"
    return {clean_name: raw}


def _parse_endpoint_port(value: str | None) -> tuple[str | None, int | None]:
    if not isinstance(value, str) or not value.strip():
        return None, None
    text = value.strip()
    for scheme in ("socks5://", "http://", "udp://", "tcp://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme):]
    text = text.split("/", 1)[0]
    if ":" not in text:
        return None, None
    host, port_str = text.rsplit(":", 1)
    host = host.strip("[]") or "127.0.0.1"
    try:
        port = int(port_str)
    except ValueError:
        return None, None
    if not 1 <= port <= 65535:
        return None, None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        host = "127.0.0.1"
    return host, port


def _extract_localdns(files: dict[str, bytes]) -> tuple[dict[str, str], dict[str, str], dict, dict[str, bytes]]:
    localdns_raw: dict[str, bytes] = {}
    remaining: dict[str, bytes] = {}
    has_root_localdns = "config/localdns.json" in files or (
        len(files) == 1 and next(iter(files)).endswith("localdns.json")
    )
    for path, body in files.items():
        if path.startswith("localdns/"):
            if not path.startswith("localdns/generated/"):
                localdns_raw[path] = body
        elif has_root_localdns and (
            path.startswith(("config/", "rules/")) or path.endswith("localdns.json")
        ):
            target = "localdns/config/localdns.json" if path.endswith("localdns.json") and "/" not in path else f"localdns/{path}"
            localdns_raw[target] = body
        else:
            remaining[path] = body

    docs: dict[str, str] = {}
    assets: dict[str, str] = {}
    for path, body in localdns_raw.items():
        safe_path(path)
        if path.lower().endswith((".srs", ".mrs", ".db", ".bin")):
            assets[path] = base64.b64encode(body).decode("ascii")
        else:
            try:
                docs[path] = body.decode("utf-8-sig")
            except UnicodeError:
                assets[path] = base64.b64encode(body).decode("ascii")

    wiring: dict = {}
    if "localdns/config/localdns.json" in docs:
        try:
            service = json_document(docs["localdns/config/localdns.json"])
            dns_cfg = service.get("dns", {}) if isinstance(service, dict) else {}
            raw_listen = dns_cfg.get("udp_listen") or dns_cfg.get("tcp_listen") or dns_cfg.get("listen")
            if isinstance(raw_listen, list) and raw_listen:
                raw_listen = raw_listen[0]
            _, listen_port = _parse_endpoint_port(raw_listen)
            if listen_port:
                wiring["localdns_listen_port"] = listen_port
                if 1024 <= listen_port <= 65535:
                    wiring["dns_port"] = listen_port
        except ConfigError:
            pass
    for ov_path in ("localdns/config/settings/overrides.json", "localdns/config/upstreams/proxy.json"):
        if ov_path not in docs:
            continue
        try:
            doc = json_document(docs[ov_path])
        except ConfigError:
            continue
        if not isinstance(doc, dict):
            continue
        socks = doc.get("socks5")
        if not socks and isinstance(doc.get("upstreams"), list):
            for u in doc["upstreams"]:
                if isinstance(u, dict) and u.get("socks5"):
                    socks = u["socks5"]
                    break
        host, port = _parse_endpoint_port(socks)
        if host and port:
            wiring["socks5"] = f"{host}:{port}"
            if 1024 <= port <= 65535:
                wiring["mixed_port"] = port
        if isinstance(doc.get("upstreams"), list):
            for u in doc["upstreams"]:
                if isinstance(u, dict) and u.get("addr"):
                    _, uport = _parse_endpoint_port(u["addr"])
                    if uport and 1024 <= uport <= 65535:
                        wiring.setdefault("dns_port", uport)
                    break

    return docs, assets, wiring, remaining


def _normalize_mihomo_path(raw_path: str) -> str:
    cleaned = raw_path.strip().replace("\\", "/")
    for prefix in ("/etc/mihomo/", "/opt/mihomo/", "/var/lib/mihomo/", "/usr/local/etc/mihomo/", "./"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
    cleaned = cleaned.lstrip("/")
    parts = [p for p in cleaned.split("/") if p not in {"", ".", ".."}]
    if not parts:
        return "ruleset/imported.yaml"
    candidate = "/".join(parts)
    if candidate in {"mihomo.yaml", "manifest.json", "README.txt"}:
        candidate = "assets/" + candidate
    return safe_path(candidate)


def _parse_seconds(value, default: int = 300) -> int:
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str):
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|h|m|s)", value.strip())
        if parts:
            total = int(sum(float(n) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[u] for n, u in parts))
            return max(1, total)
        if value.strip().isdigit():
            return max(1, int(value.strip()))
    return default


def _build_mihomo_native(config: dict, raw_yaml: str, extra_files: dict[str, bytes], localdns_docs: dict[str, str], localdns_assets: dict[str, str], wiring: dict) -> dict:
    mihomo = deepcopy(config)
    raw_rules = mihomo.pop("rules", None)
    if not isinstance(raw_rules, list):
        raw_rules = []
    rules = [str(line).strip() for line in raw_rules if isinstance(line, str) and line.strip() and "\n" not in line]
    if not rules or not rules[-1].startswith(("MATCH,", "FINAL,")):
        rules.append("MATCH,DIRECT")

    for key in ("proxy-groups", "proxies", "listeners"):
        if key in mihomo and not isinstance(mihomo[key], list):
            mihomo.pop(key, None)
    for key in ("proxy-providers", "rule-providers"):
        if key in mihomo and not isinstance(mihomo[key], dict):
            mihomo.pop(key, None)

    for section in ("proxy-providers", "rule-providers"):
        prefix = "providers" if section == "proxy-providers" else "ruleset"
        for pname, provider in mihomo.get(section, {}).items():
            if not isinstance(provider, dict):
                continue
            if isinstance(provider.get("path"), str) and provider["path"].strip():
                provider["path"] = _normalize_mihomo_path(provider["path"])
            elif provider.get("type") == "http":
                safe_seg = re.sub(r"[^A-Za-z0-9._@-]+", "_", str(pname)).strip("._") or "provider"
                provider["path"] = f"{prefix}/{safe_seg}.yaml"
    for listener in mihomo.get("listeners", []):
        if not isinstance(listener, dict):
            continue
        for field in ("certificate", "private-key"):
            val = listener.get(field)
            if isinstance(val, str) and val.strip() and "\n" not in val:
                listener[field] = _normalize_mihomo_path(val)
    if isinstance(mihomo.get("external-ui"), str) and mihomo["external-ui"].strip():
        from .native_configuration import normalize_ui_path
        mihomo["external-ui"] = normalize_ui_path(mihomo["external-ui"])

    if wiring.get("mixed_port") and "mixed-port" not in mihomo and "port" not in mihomo and "socks-port" not in mihomo:
        mihomo["mixed-port"] = wiring["mixed_port"]

    documents = {"archive/original-mihomo.yaml": raw_yaml, **localdns_docs}
    assets = dict(localdns_assets)
    for name, body in extra_files.items():
        norm = _normalize_mihomo_path(name)
        if norm.lower().endswith((".srs", ".mrs", ".db", ".bin")):
            assets[norm] = base64.b64encode(body).decode("ascii")
        else:
            try:
                documents[norm] = body.decode("utf-8-sig")
            except UnicodeError:
                assets[norm] = base64.b64encode(body).decode("ascii")

    for pname, provider in mihomo.get("proxy-providers", {}).items():
        if not isinstance(provider, dict) or provider.get("type") != "http":
            continue
        clean_path = str(provider.get("path") or "").removeprefix("./")
        if clean_path and clean_path not in documents and clean_path not in assets and not clean_path.lower().endswith((".mrs", ".srs")):
            documents[clean_path] = "proxies: []\n"
    for rname, provider in mihomo.get("rule-providers", {}).items():
        if not isinstance(provider, dict) or provider.get("type") != "http":
            continue
        clean_path = str(provider.get("path") or "").removeprefix("./")
        if clean_path and clean_path not in documents and clean_path not in assets and not clean_path.lower().endswith((".mrs", ".srs")):
            behavior = str(provider.get("behavior") or "domain").lower()
            if behavior == "ipcidr":
                documents[clean_path] = "payload:\n  - '127.0.0.1/32'\n"
            elif behavior == "classical":
                documents[clean_path] = "payload:\n  - 'DOMAIN-SUFFIX,example.invalid'\n"
            else:
                documents[clean_path] = "payload:\n  - '+.example.invalid'\n"

    has_dns = "localdns/config/localdns.json" in documents
    native = {
        "format": "mihomo-localdns-v1" if has_dns else "mihomo-v1",
        "mihomo": mihomo,
        "policies": [
            {
                "id": "imp_" + hashlib.sha256((str(i) + "\0" + text).encode("utf-8")).hexdigest()[:20],
                "text": text,
                "enabled": True,
            }
            for i, text in enumerate(rules)
        ],
        "documents": documents,
        "assets": assets,
        "origin": {"source": "imported-file"},
    }
    return checked_native(native)


def _convert_mihomo_to_profile(config: dict, extra_files: dict[str, bytes], wiring: dict, root: Path) -> dict:
    profile = empty_profile()
    profile["groups"] = []
    settings = profile["settings"]
    mixed_port = wiring.get("mixed_port") or config.get("mixed-port") or config.get("port") or config.get("socks-port")
    if isinstance(mixed_port, int) and 1024 <= mixed_port <= 65535:
        settings["mixed_port"] = mixed_port
    dns_cfg = config.get("dns", {}) if isinstance(config.get("dns"), dict) else {}
    _, dns_port = _parse_endpoint_port(dns_cfg.get("listen"))
    if wiring.get("dns_port"):
        dns_port = wiring["dns_port"]
    if isinstance(dns_port, int) and 1024 <= dns_port <= 65535 and dns_port != settings.get("mixed_port", 17890):
        settings["dns_port"] = dns_port
    if wiring.get("socks5"):
        settings["dns_socks5"] = wiring["socks5"]

    normalized_extra = {_normalize_mihomo_path(k): v for k, v in extra_files.items()}
    provider_source_ids: dict[str, str] = {}
    for pname, pdef in (config.get("proxy-providers") or {}).items():
        if not isinstance(pdef, dict):
            raise ConfigError("节点来源需要对象；请使用原生导入保留原件")
        url = str(pdef.get("url") or "").strip()
        parsed_url = urlsplit(url)
        if parsed_url.scheme in {"http", "https"} and parsed_url.hostname and not parsed_url.username and not parsed_url.password:
            sid = "s_" + hashlib.sha256(pname.encode("utf-8")).hexdigest()[:12]
            interval_hours = max(1, min(720, round(int(pdef.get("interval") or 86400) / 3600) or 24))
            src = clean_source({"id": sid, "name": str(pname)[:80], "url": url, "format": "clash", "interval_hours": interval_hours})
            profile["sources"].append(src)
            provider_source_ids[pname] = sid
            path_key = _normalize_mihomo_path(pdef["path"]) if isinstance(pdef.get("path"), str) and pdef["path"].strip() else ""
            if path_key and path_key in normalized_extra:
                try:
                    cached = yaml.safe_load(normalized_extra[path_key].decode("utf-8-sig"))
                    if not isinstance(cached, dict) or not isinstance(cached.get("proxies"), list):
                        raise ConfigError("节点来源缓存需要 proxies 列表")
                    for raw_node in cached["proxies"]:
                        node = normalize(clash_node(raw_node), sid)
                        profile["nodes"].append(node)
                except Exception:
                    raise ConfigError("节点来源缓存无法完整转换；请使用原生导入保留原件") from None

    node_by_name: dict[str, str] = {}
    seen_node_ids: set[str] = {n["id"] for n in profile["nodes"]}
    for n in profile["nodes"]:
        node_by_name[n["name"]] = n["id"]

    for raw_node in config.get("proxies") or []:
        if not isinstance(raw_node, dict):
            raise ConfigError("节点记录需要对象")
        try:
            node = normalize(clash_node(raw_node), "manual")
        except (ValueError, TypeError, AttributeError, KeyError):
            raise ConfigError("有节点无法完整转换；请使用原生导入保留协议与扩展") from None
        if node["id"] in seen_node_ids:
            node["id"] = "n_" + hashlib.sha256((node["id"] + "\0" + node["name"]).encode("utf-8")).hexdigest()[:20]
        seen_node_ids.add(node["id"])
        node_by_name[node["name"]] = node["id"]
        profile["nodes"].append(node)

    raw_groups = config.get("proxy-groups") or []
    if not isinstance(raw_groups, list) or any(not isinstance(g, dict) or not isinstance(g.get("name"), str) or not g["name"].strip() for g in raw_groups):
        raise ConfigError("节点组需要带名称的对象列表")
    if len({g["name"].strip() for g in raw_groups}) != len(raw_groups):
        raise ConfigError("节点组名称重复，不能合并为同一出口")
    group_by_name: dict[str, str] = {}
    for idx, g in enumerate(raw_groups):
        gid = f"g_{idx + 1}_" + hashlib.sha256(g["name"].strip().encode("utf-8")).hexdigest()[:10]
        group_by_name[g["name"].strip()] = gid

    groups = []
    for g in raw_groups:
        gname = g["name"].strip()
        gid = group_by_name[gname]
        gmode = str(g.get("type") or "select").lower()
        if gmode not in MODES:
            raise ConfigError("节点组方式无法等价转换，请使用原生导入")
        if any(u not in provider_source_ids for u in g.get("use", [])):
            raise ConfigError("节点组引用了无法转换的节点来源，请使用原生导入")
        sources = [provider_source_ids[u] for u in (g.get("use") or [])]
        nodes, child_groups, order = [], [], []
        for member in g.get("proxies") or []:
            if not isinstance(member, str):
                raise ConfigError("节点组成员需要文本")
            if member in node_by_name:
                nid = node_by_name[member]
                if nid not in nodes:
                    nodes.append(nid)
                    order.append(nid)
            elif member in group_by_name and group_by_name[member] != gid:
                cid = group_by_name[member]
                if cid not in child_groups:
                    child_groups.append(cid)
                    order.append(cid)
            else:
                raise ConfigError("节点组包含无法保留的成员；请使用原生导入，不能删减候选出口")
        group_obj = {
            "id": gid,
            "name": gname[:80],
            "mode": gmode,
            "sources": sources,
            "nodes": nodes,
            "groups": child_groups,
            "include": str(g.get("filter") or "")[:500],
            "exclude": str(g.get("exclude-filter") or "")[:500],
            "interval": _parse_seconds(g.get("interval"), 300),
            "tolerance": max(0, int(g.get("tolerance") or 50)) if isinstance(g.get("tolerance"), int) else 50,
            "order": order,
        }
        groups.append(group_obj)

    profile["groups"] = groups
    _mark_empty_groups_disabled(profile)
    if any(not group.get("enabled", True) for group in profile["groups"]):
        raise ConfigError("节点组缺少可转换的真实成员，请使用原生导入保留原组")

    rule_provider_ids: dict[str, str] = {}
    for rname, rdef in (config.get("rule-providers") or {}).items():
        if not isinstance(rdef, dict):
            raise ConfigError("规则来源需要对象")
        lid = "l_" + hashlib.sha256(str(rname).encode("utf-8")).hexdigest()[:16]
        entries = []
        if isinstance(rdef.get("payload"), list):
            try:
                body = yaml.safe_dump({"payload": rdef["payload"]}, allow_unicode=True).encode("utf-8")
                parsed_item, _ = parse_rules(body, "clash", root)
                entries = parsed_item["entries"]
            except ConfigError:
                raise ConfigError("规则来源缓存无法完整转换；请使用原生导入") from None
        elif isinstance(rdef.get("path"), str) and rdef["path"].strip():
            path_key = _normalize_mihomo_path(rdef["path"])
            if path_key in normalized_extra:
                try:
                    parsed_item, _ = parse_rules(normalized_extra[path_key], "auto", root)
                    entries = parsed_item["entries"]
                except ConfigError:
                    raise ConfigError("规则来源缓存无法完整转换；请使用原生导入") from None
        if not entries:
            raise ConfigError("规则来源缺少可转换的缓存；请使用原生导入保留来源")
        lib_item = {"id": lid, "name": str(rname)[:100], "kind": "classical", "entries": entries}
        url = str(rdef.get("url") or "").strip()
        parsed_url = urlsplit(url)
        if parsed_url.scheme in {"http", "https"} and parsed_url.hostname and not parsed_url.username and not parsed_url.password:
            try:
                lib_item["source"] = clean_rule_source({
                    "type": "url",
                    "url": url,
                    "format": "mrs" if rdef.get("format") == "mrs" else "clash" if rdef.get("format", "yaml") == "yaml" else "auto",
                    "auto_update": True,
                    "interval_minutes": max(1, min(43200, round(int(rdef.get("interval") or 86400) / 60) or 1440)),
                })
            except ConfigError:
                raise ConfigError("规则来源设置无法转换；请使用原生导入") from None
        profile["library"].append(lib_item)
        rule_provider_ids[str(rname)] = lid

    def resolve_target(raw_target: str) -> str:
        t = raw_target.strip()
        if t == "DIRECT":
            return "DIRECT"
        if t == "REJECT":
            return "REJECT"
        if t in group_by_name:
            gid = group_by_name[t]
            gobj = next((g for g in profile["groups"] if g["id"] == gid), None)
            if gobj and gobj.get("enabled", True):
                return gid
        if t in node_by_name:
            return node_by_name[t]
        raise ConfigError("规则出口不存在、已停用或无法等价转换；请使用原生导入核对出口")

    fallback = "DIRECT"
    policy_order: list[dict] = []
    has_fallback = False

    for idx, line in enumerate(config.get("rules") or []):
        if not isinstance(line, str) or not line.strip():
            raise ConfigError(f"第 {idx + 1} 条规则需要非空文本")
        if has_fallback:
            raise ConfigError("MATCH / FINAL 后仍有规则，无法保留顺序；请使用原生导入")
        body, comma, raw_target = line.strip().rpartition(",")
        no_resolve = raw_target.strip() == "no-resolve"
        if no_resolve:
            body, comma, raw_target = body.rpartition(",")
        if not comma or raw_target.strip() in {"src", "dst"}:
            raise ConfigError(f"第 {idx + 1} 条规则解析选项无法等价转换，请使用原生导入")
        kind, separator, value = body.partition(",")
        kind, value = kind.strip().upper(), value.strip()
        if kind in {"MATCH", "FINAL"} and not separator and not no_resolve:
            fallback = resolve_target(raw_target)
            if fallback == "REJECT":
                raise ConfigError("便携配置不支持 REJECT 兜底，请使用原生导入")
            has_fallback = True
            continue
        if not separator or kind not in MATCHES:
            raise ConfigError(f"第 {idx + 1} 条规则无法等价转换，请使用原生导入保留完整规则")
        target = resolve_target(raw_target)
        contains_ip = kind.startswith("IP-CIDR") or (kind == "RULE-SET" and any(entry.startswith(("IP-CIDR,", "IP-CIDR6,")) for item in profile["library"] if item["id"] == rule_provider_ids.get(value) for entry in item["entries"]))
        if contains_ip != no_resolve:
            raise ConfigError(f"第 {idx + 1} 条规则的 no-resolve 语义无法等价转换，请使用原生导入")
        if kind == "RULE-SET" and value in rule_provider_ids:
            lid = rule_provider_ids[value]
            policy_order.append({
                "id": f"p_{idx + 1}",
                "name": str(value)[:80],
                "type": "RULE-SET",
                "value": lid,
                "target": target,
                "enabled": True,
            })
        elif kind in MATCHES - {"RULE-SET"}:
            entry = f"{kind},{value}"
            try:
                Profile({"schema_version": 1, "library": [{"id": "chk", "name": "chk", "kind": "classical", "entries": [entry]}], "fallback": "DIRECT"})
            except ConfigError:
                raise ConfigError(f"第 {idx + 1} 条规则匹配值无效，未创建空间") from None
            # Do not merge nonadjacent rules: intervening first matches matter.
            lid = f"l_inline_{idx + 1}"
            lib_name = f"导入规则 {idx + 1} ({kind})"
            profile["library"].append({"id": lid, "name": lib_name, "kind": "classical", "entries": [entry]})
            policy_order.append({"id": f"p_{idx + 1}", "name": lib_name, "type": "RULE-SET", "value": lid, "target": target, "enabled": True})
        else:
            raise ConfigError(f"第 {idx + 1} 条规则引用的来源没有可转换快照，请使用原生导入")

    profile["policies"] = policy_order
    if not has_fallback:
        raise ConfigError("缺少明确的 MATCH / FINAL 兜底，请使用原生导入")
    profile["fallback"] = fallback
    return Profile(profile).data


def _mark_empty_groups_disabled(profile: dict) -> None:
    nodes_by_source = {n.get("source") for n in profile.get("nodes", [])}
    valid_nodes = {n["id"] for n in profile.get("nodes", [])}
    groups_by_id = {g["id"]: g for g in profile.get("groups", [])}

    def has_members(gid: str, trail: tuple[str, ...] = ()) -> bool:
        if gid in trail or gid not in groups_by_id:
            return False
        g = groups_by_id[gid]
        if any(nid in valid_nodes for nid in g.get("nodes", [])):
            return True
        if any(sid in nodes_by_source for sid in g.get("sources", [])):
            return True
        return any(has_members(cid, (*trail, gid)) for cid in g.get("groups", []))

    for g in profile.get("groups", []):
        if not has_members(g["id"]):
            g["enabled"] = False
    for g in profile.get("groups", []):
        g["groups"] = [cid for cid in g.get("groups", []) if groups_by_id.get(cid, {}).get("enabled", True)]
        g["order"] = [m for m in g.get("order", []) if m in valid_nodes or m in g["groups"]]
        if g.get("selected") and g["selected"] not in g["order"] and g["selected"] not in g.get("nodes", []):
            g.pop("selected", None)


def _convert_singbox_to_profile(config: dict, extra_files: dict[str, bytes], wiring: dict, root: Path) -> dict:
    profile = empty_profile()
    profile["groups"] = []
    settings = profile["settings"]

    for inbound in config.get("inbounds") or []:
        if not isinstance(inbound, dict):
            continue
        if inbound.get("type") in {"mixed", "socks", "http"}:
            port = inbound.get("listen_port") or inbound.get("port")
            if isinstance(port, int) and 1024 <= port <= 65535:
                settings["mixed_port"] = port
                break

    if wiring.get("mixed_port"):
        settings["mixed_port"] = wiring["mixed_port"]
    if wiring.get("dns_port"):
        settings["dns_port"] = wiring["dns_port"]
    if wiring.get("socks5"):
        settings["dns_socks5"] = wiring["socks5"]

    if settings.get("mixed_port", 17890) == settings.get("dns_port", 1053):
        settings["dns_port"] = 1053 if settings["mixed_port"] != 1053 else 5353

    tag_map: dict[str, str] = {}
    seen_node_ids: set[str] = set()
    raw_groups: list[tuple[str, dict]] = []

    for idx, ob in enumerate(config.get("outbounds") or []):
        if not isinstance(ob, dict):
            continue
        otype = str(ob.get("type") or "").lower()
        tag = str(ob.get("tag") or otype or f"outbound_{idx + 1}").strip()
        if otype == "direct":
            tag_map[tag] = "DIRECT"
        elif otype == "block":
            tag_map[tag] = "REJECT"
        elif otype in SUPPORTED:
            try:
                node = normalize(singbox_node(ob), "manual")
            except (ValueError, TypeError, AttributeError, KeyError):
                raise ConfigError("有 sing-box 节点无法完整转换，未创建空间") from None
            if node["id"] in seen_node_ids:
                node["id"] = "n_" + hashlib.sha256((node["id"] + "\0" + tag).encode("utf-8")).hexdigest()[:20]
            seen_node_ids.add(node["id"])
            profile["nodes"].append(node)
            tag_map[tag] = node["id"]
        elif otype in {"selector", "urltest", "url-test", "fallback"}:
            gid = f"g_{len(raw_groups) + 1}_" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:10]
            tag_map[tag] = gid
            raw_groups.append((gid, ob))
        else:
            raise ConfigError("包含不支持的 sing-box 出口，不能静默丢弃或替换")

    node_ids = {n["id"] for n in profile["nodes"]}
    group_ids = {gid for gid, _ in raw_groups}

    for gid, ob in raw_groups:
        otype = str(ob.get("type") or "selector").lower()
        gmode = "select" if otype == "selector" else "url-test" if otype in {"urltest", "url-test"} else "fallback"
        tag = str(ob.get("tag") or gid).strip()
        nodes, child_groups, order = [], [], []
        for member_tag in ob.get("outbounds") or []:
            mapped = tag_map.get(str(member_tag))
            if not mapped or mapped in BUILTINS or mapped == gid:
                raise ConfigError("sing-box 组成员无法等价转换，不能删减候选出口")
            if mapped in node_ids and mapped not in nodes:
                nodes.append(mapped)
                order.append(mapped)
            elif mapped in group_ids and mapped not in child_groups:
                child_groups.append(mapped)
                order.append(mapped)
        group_obj = {
            "id": gid,
            "name": tag[:80],
            "mode": gmode,
            "sources": [],
            "nodes": nodes,
            "groups": child_groups,
            "include": "",
            "exclude": "",
            "interval": _parse_seconds(ob.get("interval"), 300),
            "tolerance": max(0, int(ob.get("tolerance") or 50)) if isinstance(ob.get("tolerance"), int) else 50,
            "order": order,
        }
        if isinstance(ob.get("url"), str) and urlsplit(ob["url"]).scheme in {"http", "https"}:
            group_obj["test_url"] = ob["url"]
        default_member = tag_map.get(str(ob.get("default") or ""))
        if gmode == "select" and default_member in order:
            group_obj["selected"] = default_member
        profile["groups"].append(group_obj)

    _mark_empty_groups_disabled(profile)

    normalized_extra = {_clean_archive_path(k) or k: v for k, v in extra_files.items()}
    route = config.get("route") if isinstance(config.get("route"), dict) else {}
    ruleset_map: dict[str, str] = {}

    for idx, rs in enumerate(route.get("rule_set") or []):
        if not isinstance(rs, dict) or not isinstance(rs.get("tag"), str):
            raise ConfigError("sing-box 规则来源需要带 tag 的对象")
        rtag = rs["tag"].strip()
        lid = f"l_rs_{idx + 1}_" + hashlib.sha256(rtag.encode("utf-8")).hexdigest()[:10]
        entries = []
        if isinstance(rs.get("path"), str) and rs["path"].strip():
            rel = _clean_archive_path(rs["path"].removeprefix("./")) or rs["path"].removeprefix("./")
            body = normalized_extra.get(rel) or normalized_extra.get(Path(rel).name)
            if body:
                try:
                    parsed_item, _ = parse_rules(body, "auto", root)
                    entries = parsed_item["entries"]
                except ConfigError:
                    raise ConfigError("sing-box 规则缓存无法完整转换，未创建空间") from None
        elif isinstance(rs.get("rules"), list):
            try:
                body = json.dumps({"version": 3, "rules": rs["rules"]}).encode("utf-8")
                parsed_item, _ = parse_rules(body, "sing-box", root)
                entries = parsed_item["entries"]
            except ConfigError:
                raise ConfigError("sing-box 规则集无法完整转换，未创建空间") from None
        if not entries:
            raise ConfigError("sing-box 规则来源缺少可转换快照，请先提供完整缓存")
        lib_item = {"id": lid, "name": rtag[:100], "kind": "classical", "entries": entries}
        url = str(rs.get("url") or "").strip()
        parsed_url = urlsplit(url)
        if parsed_url.scheme in {"http", "https"} and parsed_url.hostname and not parsed_url.username and not parsed_url.password:
            try:
                lib_item["source"] = clean_rule_source({
                    "type": "url",
                    "url": url,
                    "format": "srs" if rs.get("format") == "binary" or url.endswith(".srs") else "sing-box",
                    "auto_update": True,
                    "interval_minutes": 1440,
                })
            except ConfigError:
                raise ConfigError("sing-box 规则来源设置无效，未创建空间") from None
        profile["library"].append(lib_item)
        ruleset_map[rtag] = lid

    def resolve_target(rule_dict: dict) -> str:
        if rule_dict.get("action") == "reject":
            return "REJECT"
        mapped = tag_map.get(str(rule_dict.get("outbound") or ""))
        if mapped in BUILTINS:
            return mapped
        if mapped in group_ids:
            gobj = next((g for g in profile["groups"] if g["id"] == mapped), None)
            if gobj and gobj.get("enabled", True):
                return mapped
        if mapped in node_ids:
            return mapped
        raise ConfigError("sing-box 规则出口不存在或已停用，不能替换为其他出口")

    field_map = {
        "domain": "DOMAIN",
        "domain_suffix": "DOMAIN-SUFFIX",
        "domain_keyword": "DOMAIN-KEYWORD",
        "domain_regex": "DOMAIN-REGEX",
        "ip_cidr": "IP-CIDR",
    }

    policies = []
    for idx, rule in enumerate(route.get("rules") or []):
        if not isinstance(rule, dict):
            raise ConfigError("sing-box 路由规则需要对象")
        if set(rule) - {*field_map, "rule_set", "action", "outbound"} or rule.get("action", "route") not in {"route", "reject"}:
            raise ConfigError(f"第 {idx + 1} 条 sing-box 规则无法等价转换，未创建空间")
        target = resolve_target(rule)
        condition_groups = []
        rs_refs = rule.get("rule_set")
        if isinstance(rs_refs, str):
            rs_refs = [rs_refs]
        if rs_refs is not None:
            if not isinstance(rs_refs, list) or not rs_refs:
                raise ConfigError("sing-box rule_set 引用需要非空列表")
            conditions = []
            for ref in rs_refs:
                if not isinstance(ref, str) or ref not in ruleset_map:
                    raise ConfigError("sing-box 规则引用了没有快照的来源")
                conditions.append({"type": "RULE-SET", "value": ruleset_map[ref]})
            condition_groups.append({"operator": "or", "conditions": conditions})
        domain_conditions, ip_conditions = [], []
        for field, kind in field_map.items():
            vals = rule.get(field)
            if isinstance(vals, str):
                vals = [vals]
            if vals is not None:
                if not isinstance(vals, list) or not vals or any(not isinstance(v, str) or not v.strip() for v in vals):
                    raise ConfigError("sing-box 匹配值需要非空文本列表")
                for v in vals:
                    actual_kind = "IP-CIDR6" if kind == "IP-CIDR" and ":" in v else kind
                    condition = {"type": actual_kind, "value": v.strip()}
                    (ip_conditions if kind == "IP-CIDR" else domain_conditions).append(condition)
        for conditions in (domain_conditions, ip_conditions):
            if conditions:
                condition_groups.append({"operator": "or", "conditions": conditions})
        if not condition_groups:
            raise ConfigError("sing-box 无匹配条件的路由无法转换为普通策略，请明确 final 兜底")
        policies.append({"id": f"p_{idx + 1}", "name": f"分流规则 {idx + 1}", "type": "LOGICAL", "group_operator": "and", "condition_groups": condition_groups, "target": target, "enabled": True})

    profile["policies"] = policies
    first_tag = (config.get("outbounds") or [{}])[0].get("tag")
    fallback = resolve_target({"outbound": route.get("final") or first_tag})
    if fallback == "REJECT":
        raise ConfigError("便携配置不支持 REJECT 兜底，未创建空间")
    profile["fallback"] = fallback
    return Profile(profile).data


def _attach_localdns_to_portable(profile: dict, localdns_docs: dict[str, str], localdns_assets: dict[str, str], root: Path) -> bool:
    if "localdns/config/localdns.json" not in localdns_docs:
        return False
    try:
        from .mosdns_import import import_localdns
        temp_profile = {
            "schema_version": 1,
            "fallback": "DIRECT",
            "settings": profile.get("settings", {}),
            "native_configuration": {
                "format": "mihomo-localdns-v1",
                "documents": localdns_docs,
                "assets": localdns_assets,
            },
        }
        migrated = import_localdns(temp_profile, root)
        mosdns_cfg = migrated["settings"]
        profile["mosdns"] = mosdns_cfg
        return True
    except Exception:
        raise ConfigError("LocalDNS 未能生成迁移草稿，未创建空间；请使用原生导入保留原件") from None


def _portable_profile(converter, config, files, wiring, root):
    try:
        return converter(config, files, wiring, root)
    except ConfigError:
        raise
    except (ValueError, TypeError, AttributeError, KeyError, IndexError, RecursionError):
        raise ConfigError("配置字段类型或引用无效，未创建空间；Mihomo 配置可改用原生导入保留原件") from None


def import_configuration_space(spaces, request: dict, root: Path) -> dict:
    """Import Mihomo, sing-box, LocalDNS, or YoMesh profile into a new space."""
    name = str(request.get("name") or "").strip()
    description = str(request.get("description") or "").strip()
    revision = request.get("revision")
    mode = str(request.get("mode") or "auto").strip()
    if mode not in {"auto", "portable", "native"}:
        raise ConfigError("导入模式需要为 auto、portable 或 native")

    raw_main = _decode_payload(request.get("content_base64"), request.get("text") or request.get("content"), "配置文件")
    main_files = _unpack_files(raw_main, str(request.get("filename") or ""))
    localdns_docs, localdns_assets, wiring, remaining = _extract_localdns(main_files)

    dns_text = request.get("localdns_text") or request.get("localdns_content")
    if request.get("localdns_base64") or dns_text:
        raw_dns = _decode_payload(request.get("localdns_base64"), dns_text, "LocalDNS 配置")
        dns_files = _unpack_files(raw_dns, str(request.get("localdns_filename") or "localdns.json"))
        extra_docs, extra_assets, extra_wiring, _ = _extract_localdns(dns_files)
        localdns_docs.update(extra_docs)
        localdns_assets.update(extra_assets)
        wiring.update(extra_wiring)

    has_localdns = bool(localdns_docs)

    # If the user uploaded a LocalDNS-only package without a proxy file, create a default space wired to it.
    if not remaining and "localdns/config/localdns.json" in localdns_docs:
        default_mihomo = {
            "mixed-port": wiring.get("mixed_port", 17890),
            "proxies": [],
            "proxy-providers": {},
            "proxy-groups": [{"name": "默认代理", "type": "select", "proxies": ["DIRECT"]}],
            "rule-providers": {},
            "listeners": [],
            "rules": ["MATCH,DIRECT"],
        }
        native = _build_mihomo_native(
            default_mihomo,
            yaml.safe_dump(default_mihomo, allow_unicode=True, sort_keys=False),
            {},
            localdns_docs,
            localdns_assets,
            wiring,
        )
        result = spaces.import_native(native, name, revision, description or "从 LocalDNS 配置导入")
        return {
            **result,
            "detected": "localdns",
            "wiring": wiring,
            "import_summary": {
                "format": "LocalDNS",
                "mode": "native",
                "nodes": 0,
                "groups": 1,
                "localdns_attached": True,
            },
        }

    # Find the primary configuration file among remaining files
    primary_name = None
    for candidate in ("mihomo.yaml", "config.yaml", "config.yml", "clash.yaml", "sing-box.json", "config.json", "profile.json"):
        if candidate in remaining:
            primary_name = candidate
            break
    if primary_name is None:
        for path in sorted(remaining):
            if path.lower().endswith((".yaml", ".yml", ".json")) and "/" not in path:
                primary_name = path
                break
    if primary_name is None:
        for path in sorted(remaining):
            if path.lower().endswith((".yaml", ".yml", ".json")):
                primary_name = path
                break
    if primary_name is None:
        raise ConfigError("未找到可识别的 Mihomo (.yaml) 或 sing-box (.json) 主配置文件")

    primary_bytes = remaining.pop(primary_name)
    try:
        primary_text = primary_bytes.decode("utf-8-sig")
    except UnicodeError:
        raise ConfigError("主配置文件需要为 UTF-8 编码文本") from None

    stripped = primary_text.lstrip()
    if stripped.startswith("{"):
        try:
            parsed_json = json.loads(primary_text)
        except json.JSONDecodeError:
            raise ConfigError("JSON 配置文件格式无效，请检查括号与逗号") from None
        if not isinstance(parsed_json, dict):
            raise ConfigError("JSON 配置根节点需要为对象")

        # 1. YoMesh Profile JSON
        if parsed_json.get("schema_version") == 1:
            if "native_configuration" in parsed_json:
                native_cfg = parsed_json["native_configuration"]
                result = spaces.import_native(
                    native_cfg,
                    name,
                    revision,
                    description or "从 YoMesh 原生配置导入",
                    mosdns=parsed_json.get("mosdns"),
                )
                return {
                    **result,
                    "detected": "yomesh-native",
                    "wiring": wiring,
                    "import_summary": {
                        "format": "YoMesh 原生配置",
                        "mode": "native",
                        "nodes": len(native_cfg.get("mihomo", {}).get("proxies", [])),
                        "groups": len(native_cfg.get("mihomo", {}).get("proxy-groups", [])),
                        "localdns_attached": has_localdns or native_cfg.get("format") == "mihomo-localdns-v1",
                    },
                }
            if not isinstance(parsed_json.get("settings", {}), dict):
                raise ConfigError("Profile settings 需要对象，未创建空间")
            if wiring.get("mixed_port"):
                parsed_json.setdefault("settings", {})["mixed_port"] = wiring["mixed_port"]
            if wiring.get("dns_port"):
                parsed_json.setdefault("settings", {})["dns_port"] = wiring["dns_port"]
            if wiring.get("socks5"):
                parsed_json.setdefault("settings", {})["dns_socks5"] = wiring["socks5"]
            attached = _attach_localdns_to_portable(parsed_json, localdns_docs, localdns_assets, root)
            result = spaces.import_profile(
                parsed_json,
                name,
                revision,
                description or "从 YoMesh 配置导入",
                proxy="mihomo",
                rule_output="local",
            )
            return {
                **result,
                "detected": "yomesh-profile",
                "wiring": wiring,
                "import_summary": {
                    "format": "YoMesh Profile",
                    "mode": "portable",
                    "nodes": len(parsed_json.get("nodes", [])),
                    "groups": len(parsed_json.get("groups", [])),
                    "localdns_attached": attached,
                },
            }

        # 2. sing-box JSON
        if any(k in parsed_json for k in ("outbounds", "route", "inbounds", "dns")):
            profile = _portable_profile(_convert_singbox_to_profile, parsed_json, remaining, wiring, root)
            attached = _attach_localdns_to_portable(profile, localdns_docs, localdns_assets, root)
            result = spaces.import_profile(
                profile,
                name,
                revision,
                description or "从 sing-box 配置导入",
                proxy="sing-box",
                rule_output="local" if profile.get("library") else "inline",
            )
            return {
                **result,
                "detected": "sing-box",
                "wiring": wiring,
                "import_summary": {
                    "format": "sing-box",
                    "mode": "portable",
                    "nodes": len(profile.get("nodes", [])),
                    "groups": len(profile.get("groups", [])),
                    "localdns_attached": attached,
                },
            }

        raise ConfigError("无法识别该 JSON 文件；请上传 sing-box 配置、LocalDNS 配置或 YoMesh Profile")

    # 3. Mihomo / Clash YAML
    try:
        parsed_yaml = yaml.safe_load(primary_text)
    except yaml.YAMLError:
        raise ConfigError("YAML 配置文件格式无效，请检查缩进与语法") from None
    if not isinstance(parsed_yaml, dict) or not any(
        k in parsed_yaml for k in ("proxies", "proxy-providers", "proxy-groups", "rules", "mixed-port", "port", "listeners")
    ):
        raise ConfigError("无法识别该 YAML 文件；请上传有效的 Mihomo / Clash 配置文件")

    # Automatic import preserves the authoritative native document, including
    # unsupported rules and fields. Portable conversion is an explicit choice.
    use_portable = mode == "portable"
    if use_portable:
        try:
            profile = _portable_profile(_convert_mihomo_to_profile, parsed_yaml, remaining, wiring, root)
            attached = _attach_localdns_to_portable(profile, localdns_docs, localdns_assets, root)
            result = spaces.import_profile(
                profile,
                name,
                revision,
                description or "从 Mihomo 配置导入（支持 Mihomo / sing-box 双内核）",
                proxy="mihomo",
                rule_output="local" if profile.get("library") else "inline",
            )
            return {
                **result,
                "detected": "mihomo-portable",
                "wiring": wiring,
                "import_summary": {
                    "format": "Mihomo",
                    "mode": "portable",
                    "nodes": len(profile.get("nodes", [])),
                    "groups": len(profile.get("groups", [])),
                    "localdns_attached": attached,
                },
            }
        except ConfigError:
            if mode == "portable":
                raise

    native = _build_mihomo_native(parsed_yaml, primary_text, remaining, localdns_docs, localdns_assets, wiring)
    result = spaces.import_native(
        native,
        name,
        revision,
        description or ("从 Mihomo + LocalDNS 配置导入" if "localdns/config/localdns.json" in localdns_docs else "从 Mihomo 原生配置导入"),
    )
    return {
        **result,
        "detected": "mihomo-native",
        "wiring": wiring,
        "import_summary": {
            "format": "Mihomo",
            "mode": "native",
            "nodes": len(native.get("mihomo", {}).get("proxies", [])),
            "groups": len(native.get("mihomo", {}).get("proxy-groups", [])),
            "localdns_attached": has_localdns,
        },
    }
