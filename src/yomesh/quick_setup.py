"""Quick setup wizard, preset region groups, preset rule packs, and Mihomo controller auto-reload."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from .model import ConfigError


PRESET_REGIONS = [
    {
        "key": "hk",
        "id": "g_hk",
        "name": "HK 香港节点",
        "badge": "🇭🇰 香港节点 (自动测速)",
        "short": "香港",
        "mode": "url-test",
        "filter": "香港|HK|Hong Kong|HongKong|🇭🇰",
    },
    {
        "key": "tw",
        "id": "g_tw",
        "name": "TW 台湾节点",
        "badge": "🇹🇼 台湾节点 (自动测速)",
        "short": "台湾",
        "mode": "url-test",
        "filter": "台湾|TW|Taiwan|TaiWan|台北|🇹🇼",
    },
    {
        "key": "jp",
        "id": "g_jp",
        "name": "JP 日本节点",
        "badge": "🇯🇵 日本节点 (自动测速)",
        "short": "日本",
        "mode": "url-test",
        "filter": "日本|JP|Japan|东京|大阪|🇯🇵",
    },
    {
        "key": "sg",
        "id": "g_sg",
        "name": "SG 新加坡节点",
        "badge": "🇸🇬 新加坡节点 (自动测速)",
        "short": "新加坡",
        "mode": "url-test",
        "filter": "新加坡|SG|Singapore|狮城|🇸🇬",
    },
    {
        "key": "us",
        "id": "g_us",
        "name": "US 美国节点",
        "badge": "🇺🇸 美国节点 (自动测速)",
        "short": "美国",
        "mode": "url-test",
        "filter": "美国|US|USA|United States|America|洛杉矶|硅谷|西雅图|🇺🇸",
    },
]


PRESET_RULES = [
    {
        "key": "adblock",
        "id": "preset_ad",
        "name": "广告与追踪拦截",
        "label": "🛡️ 广告拦截",
        "pill": "🛡️ 广告拦截",
        "desc": "🛡️ 广告拦截 → REJECT",
        "default_target": "REJECT",
        "default_region": "reject",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/category-ads-all.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/category-ads-all.yaml",
        "ip_url": "",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,doubleclick.net",
            "DOMAIN-SUFFIX,googlesyndication.com",
            "DOMAIN-SUFFIX,googleadservices.com",
            "DOMAIN-SUFFIX,adservice.google.com",
            "DOMAIN-SUFFIX,app-measurement.com",
        ],
    },
    {
        "key": "ai",
        "id": "preset_ai",
        "name": "AI 智能服务 (OpenAI / Claude / Gemini)",
        "label": "🤖 AI 全家桶",
        "pill": "🤖 AI (OpenAI/Claude)",
        "desc": "🤖 AI 全家桶 (OpenAI/Claude) → 🇺🇸 美国节点",
        "default_target": "us",
        "default_region": "us",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/category-ai-!cn.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/category-ai-!cn.yaml",
        "ip_url": "",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,openai.com",
            "DOMAIN-SUFFIX,chatgpt.com",
            "DOMAIN-SUFFIX,oaistatic.com",
            "DOMAIN-SUFFIX,oaiusercontent.com",
            "DOMAIN-SUFFIX,anthropic.com",
            "DOMAIN-SUFFIX,claude.ai",
            "DOMAIN-SUFFIX,gemini.google.com",
            "DOMAIN-SUFFIX,generativelanguage.googleapis.com",
            "DOMAIN-SUFFIX,cursor.com",
            "DOMAIN-SUFFIX,cursor.sh",
            "DOMAIN-SUFFIX,perplexity.ai",
        ],
    },
    {
        "key": "youtube",
        "id": "preset_youtube",
        "name": "YouTube 视频服务",
        "label": "🎬 YouTube",
        "pill": "🎬 YouTube",
        "desc": "🎬 YouTube 视频 → 🇭🇰 香港节点",
        "default_target": "hk",
        "default_region": "hk",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/youtube.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/youtube.yaml",
        "ip_url": "",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,youtube.com",
            "DOMAIN-SUFFIX,youtu.be",
            "DOMAIN-SUFFIX,googlevideo.com",
            "DOMAIN-SUFFIX,ytimg.com",
            "DOMAIN-SUFFIX,ggpht.com",
        ],
    },
    {
        "key": "netflix",
        "id": "preset_netflix",
        "name": "海外流媒体 (Netflix / Disney+ / Spotify)",
        "label": "🍿 Netflix / 流媒体",
        "pill": "🍿 Netflix",
        "desc": "🍿 Netflix 奈飞 → 🇸🇬 新加坡节点",
        "default_target": "sg",
        "default_region": "sg",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/netflix.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/netflix.yaml",
        "ip_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geoip/netflix.yaml",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,netflix.com",
            "DOMAIN-SUFFIX,netflix.net",
            "DOMAIN-SUFFIX,nflxvideo.net",
            "DOMAIN-SUFFIX,nflximg.net",
            "DOMAIN-SUFFIX,disneyplus.com",
            "DOMAIN-SUFFIX,dssott.com",
            "DOMAIN-SUFFIX,spotify.com",
            "DOMAIN-SUFFIX,scdn.co",
        ],
    },
    {
        "key": "telegram",
        "id": "preset_telegram",
        "name": "Telegram 电报消息",
        "label": "💬 Telegram",
        "pill": "💬 Telegram",
        "desc": "💬 Telegram 电报 → 🇸🇬 新加坡节点",
        "default_target": "sg",
        "default_region": "sg",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/telegram.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/telegram.yaml",
        "ip_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geoip/telegram.yaml",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,t.me",
            "DOMAIN-SUFFIX,tdesktop.com",
            "DOMAIN-SUFFIX,telegra.ph",
            "DOMAIN-SUFFIX,telegram.me",
            "DOMAIN-SUFFIX,telegram.org",
            "IP-CIDR,91.108.4.0/22",
            "IP-CIDR,91.108.8.0/22",
            "IP-CIDR,91.108.16.0/22",
            "IP-CIDR,91.108.56.0/22",
            "IP-CIDR,149.154.160.0/20",
        ],
    },
    {
        "key": "github",
        "id": "preset_github",
        "name": "GitHub 与开发者服务",
        "label": "🐙 GitHub",
        "pill": "🐙 GitHub",
        "desc": "🐙 GitHub 开发者 → 🚀 默认代理",
        "default_target": "proxy",
        "default_region": "proxy",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/github.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/github.yaml",
        "ip_url": "",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,github.com",
            "DOMAIN-SUFFIX,github.io",
            "DOMAIN-SUFFIX,githubusercontent.com",
            "DOMAIN-SUFFIX,githubassets.com",
            "DOMAIN-SUFFIX,ghcr.io",
            "DOMAIN-SUFFIX,docker.io",
            "DOMAIN-SUFFIX,docker.com",
        ],
    },
    {
        "key": "apple",
        "id": "preset_apple",
        "name": "Apple 与 Microsoft 服务",
        "label": "🍎 Apple / 微软",
        "pill": "🍎 Apple 服务",
        "desc": "🍎 Apple / 微软服务 → DIRECT 直连",
        "default_target": "DIRECT",
        "default_region": "direct",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/apple.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/apple.yaml",
        "ip_url": "",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "DOMAIN-SUFFIX,apple.com",
            "DOMAIN-SUFFIX,icloud.com",
            "DOMAIN-SUFFIX,mzstatic.com",
            "DOMAIN-SUFFIX,cdn-apple.com",
            "DOMAIN-SUFFIX,microsoft.com",
            "DOMAIN-SUFFIX,msftconnecttest.com",
            "DOMAIN-SUFFIX,windowsupdate.com",
        ],
    },
    {
        "key": "cn",
        "id": "preset_cn",
        "name": "局域网与国内直连 (CN / LAN)",
        "label": "🏠 国内直连",
        "pill": "🏠 国内域名+IP直连",
        "desc": "🏠 国内域名 & 局域网 IP → DIRECT 直连",
        "default_target": "DIRECT",
        "default_region": "direct",
        "url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/cn.yaml",
        "domain_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geosite/cn.yaml",
        "ip_url": "https://testingcf.jsdelivr.net/gh/MetaCubeX/meta-rules-dat@meta/geo/geoip/cn.yaml",
        "behavior": "domain",
        "format": "yaml",
        "entries": [
            "IP-CIDR,127.0.0.0/8",
            "IP-CIDR,10.0.0.0/8",
            "IP-CIDR,172.16.0.0/12",
            "IP-CIDR,192.168.0.0/16",
            "IP-CIDR,100.64.0.0/10",
            "DOMAIN-SUFFIX,cn",
            "DOMAIN-SUFFIX,baidu.com",
            "DOMAIN-SUFFIX,qq.com",
            "DOMAIN-SUFFIX,weixin.qq.com",
            "DOMAIN-SUFFIX,taobao.com",
            "DOMAIN-SUFFIX,tmall.com",
            "DOMAIN-SUFFIX,alipay.com",
            "DOMAIN-SUFFIX,jd.com",
            "DOMAIN-SUFFIX,bilibili.com",
            "DOMAIN-SUFFIX,163.com",
            "DOMAIN-SUFFIX,douyin.com",
        ],
    },
]


class ClientControllers:
    """Stores optional Mihomo External Controller endpoints per space for 0-step hot-reload."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.path = self.root / "data/client-controllers.json"
        self.lock = threading.RLock()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "controllers": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("controllers"), dict):
                return data
        except (OSError, ValueError):
            pass
        return {"version": 1, "controllers": {}}

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(f".controllers.{secrets.token_hex(6)}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)

    def all_items(self) -> dict:
        return self.listing()["controllers"]

    def listing(self) -> dict:
        with self.lock:
            raw = self._load()["controllers"]
            out = {}
            for space_id, cfg in raw.items():
                if isinstance(cfg, dict) and cfg.get("url"):
                    out[space_id] = {
                        "url": cfg["url"],
                        "has_secret": bool(cfg.get("secret")),
                        "enabled": bool(cfg.get("enabled", True)),
                        "last_status": cfg.get("last_status"),
                        "last_error": cfg.get("last_error", ""),
                    }
            return {"controllers": out}

    def update(self, space_id: str, payload: dict) -> dict:
        with self.lock:
            data = self._load()
            if payload.get("action") == "remove" or not str(payload.get("url") or "").strip():
                data["controllers"].pop(space_id, None)
                self._save(data)
                return {"space_id": space_id, "enabled": False, "url": "", "configured": False, **self.listing()}

            raw_url = str(payload.get("url") or "").strip().rstrip("/")
            if not re.match(r"^https?://", raw_url, re.IGNORECASE):
                raw_url = "http://" + raw_url
            parsed = urlsplit(raw_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ConfigError("请填写有效的 Mihomo 控制器地址，例如 http://192.168.1.2:9090")
            clean_url = f"{parsed.scheme}://{parsed.netloc}"
            existing = data["controllers"].get(space_id) or {}
            secret = payload.get("secret")
            if secret is None or secret == "••••••••":
                secret = existing.get("secret", "")
            else:
                secret = str(secret).strip()

            record = {
                "url": clean_url,
                "secret": secret,
                "enabled": bool(payload.get("enabled", True)),
                "last_status": existing.get("last_status"),
                "last_error": "",
            }
            probe_result = None
            if payload.get("test", True) and record["enabled"]:
                probe_result = self._probe(clean_url, secret)
                record["last_status"] = "connected" if probe_result["reachable"] else "unreachable"
                record["last_error"] = "" if probe_result["reachable"] else probe_result["message"]

            data["controllers"][space_id] = record
            self._save(data)
            return {
                "space_id": space_id,
                "url": clean_url,
                "enabled": record["enabled"],
                "last_status": record["last_status"],
                "last_error": record["last_error"],
                "configured": True,
                "probe": probe_result,
                **self.listing(),
            }

    @staticmethod
    def _probe(url: str, secret: str) -> dict:
        headers = {"Accept": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        req = Request(url + "/version", headers=headers, method="GET")
        try:
            with urlopen(req, timeout=3) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
                return {"reachable": True, "version": body.get("version", "mihomo"), "message": "连接成功"}
        except HTTPError as err:
            if err.code in (401, 403):
                return {"reachable": False, "message": "连接被拒绝（401/403），请检查 Secret 密钥"}
            return {"reachable": False, "message": f"HTTP {err.code}"}
        except (URLError, OSError, ValueError) as err:
            return {"reachable": False, "message": f"暂不可达（{err}），已保存配置"}

    def notify_reload(self, space_id: str) -> dict:
        """Best-effort non-blocking notification to Mihomo External Controller."""
        with self.lock:
            cfg = self._load()["controllers"].get(space_id)
        if not isinstance(cfg, dict) or not cfg.get("enabled", True) or not cfg.get("url"):
            return {"notified": False, "reloaded": False, "reason": "not_configured"}

        base = cfg["url"].rstrip("/")
        secret = cfg.get("secret", "")
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"

        try:
            for kind in ("rules", "proxies"):
                try:
                    req = Request(f"{base}/providers/{kind}", headers=headers, method="GET")
                    with urlopen(req, timeout=2.5) as resp:
                        pdata = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
                    for name, item in (pdata.get("providers") or {}).items():
                        if isinstance(item, dict) and item.get("vehicleType") == "HTTP":
                            put_req = Request(
                                f"{base}/providers/{kind}/{quote(str(name), safe='')}",
                                data=b"",
                                headers=headers,
                                method="PUT",
                            )
                            try:
                                with urlopen(put_req, timeout=3):
                                    pass
                            except Exception:
                                pass
                except Exception:
                    pass
            reload_req = Request(
                f"{base}/configs?force=true",
                data=json.dumps({"path": "", "payload": ""}).encode("utf-8"),
                headers=headers,
                method="PUT",
            )
            with urlopen(reload_req, timeout=3):
                pass
            return {"notified": True, "reloaded": True, "url": base}
        except Exception as err:
            return {"notified": False, "reloaded": False, "url": base, "error": str(err)}


def quick_add_region_groups(spaces, space_id: str, requested_keys: list[str] | None = None) -> dict:
    """One-click add standard HK/TW/JP/SG/US auto-test groups to either a Native or Portable space."""
    snapshot = spaces.workspace_snapshot(space_id)
    revision = snapshot["revision"]
    profile = deepcopy(snapshot["profile"])
    chosen_keys = set(requested_keys or [r["key"] for r in PRESET_REGIONS])
    regions = [r for r in PRESET_REGIONS if r["key"] in chosen_keys]

    native = profile.get("native_configuration")
    if native:
        mihomo = native.setdefault("mihomo", {})
        groups = mihomo.setdefault("proxy-groups", [])
        providers = list((mihomo.get("proxy-providers") or {}).keys())
        proxies = [p["name"] for p in (mihomo.get("proxies") or []) if isinstance(p, dict) and p.get("name")]
        existing_names = {g.get("name") for g in groups if isinstance(g, dict)}
        added = []
        for reg in regions:
            if reg["name"] in existing_names or any(reg["short"] in str(n) for n in existing_names):
                continue
            new_group = {
                "name": reg["name"],
                "type": "url-test",
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
                "filter": reg["filter"],
            }
            if providers:
                new_group["use"] = list(providers)
            elif proxies:
                matched = [n for n in proxies if re.search(reg["filter"], n, re.IGNORECASE)]
                new_group["proxies"] = matched if matched else ["DIRECT"]
            else:
                new_group["proxies"] = ["DIRECT"]
            groups.append(new_group)
            existing_names.add(reg["name"])
            native.setdefault("group_roles", {})[reg["name"]] = "nodes"
            if isinstance(native.get("selection", {}).get("groups"), list):
                if reg["name"] not in native["selection"]["groups"]:
                    native["selection"]["groups"].append(reg["name"])
            added.append(reg["name"])

        updated = spaces.save_profile(profile, revision, space_id) if added else snapshot
        return {"added": added, "count": len(added), "added_count": len(added), "workspace": spaces.decorate(updated, space_id)}

    # Portable space
    groups = profile.setdefault("groups", [])
    sources = [s["id"] for s in profile.get("sources", []) if s.get("enabled", True)]
    nodes = [n["id"] for n in profile.get("nodes", []) if n.get("enabled", True)]
    existing_names = {g.get("name") for g in groups}
    existing_ids = {g.get("id") for g in groups}
    added = []
    for reg in regions:
        if reg["name"] in existing_names or any(reg["short"] in str(n) for n in existing_names):
            continue
        gid = reg["id"]
        while gid in existing_ids:
            gid = f"{reg['id']}_{secrets.token_hex(2)}"
        groups.append({
            "id": gid,
            "name": reg["name"],
            "mode": "url-test",
            "sources": list(sources),
            "nodes": [] if sources else list(nodes[:1]),
            "groups": [],
            "include": "" if not sources and len(nodes) <= 1 else reg["filter"],
            "exclude": "",
            "interval": 300,
            "enabled": True,
        })
        existing_names.add(reg["name"])
        existing_ids.add(gid)
        if isinstance(profile.get("composition", {}).get("groups"), list):
            profile["composition"]["groups"].append(gid)
        added.append(reg["name"])

    default_group = next((g for g in groups if g["id"] == "g_default"), None)
    if default_group and added:
        for g in groups:
            if g["id"] != "g_default" and g["id"] not in default_group.get("groups", []):
                default_group.setdefault("groups", []).append(g["id"])

    updated = spaces.save_profile(profile, revision, space_id) if added else snapshot
    return {"added": added, "count": len(added), "added_count": len(added), "workspace": spaces.decorate(updated, space_id)}


def quick_create_space(spaces, publications, request: dict, default_base_url: str) -> dict:
    """Create an isolated, ready-to-use space in 1 step from subscription URL + selected regions + preset rules."""
    raw_name = str(request.get("name") or "").strip() or "快速配置方案"
    existing_names = {s.get("name") for s in spaces.snapshot().get("spaces", [])}
    name = raw_name[:80]
    if name in existing_names:
        suffix = 2
        while f"{raw_name[:74]} ({suffix})" in existing_names:
            suffix += 1
        name = f"{raw_name[:74]} ({suffix})"

    proxy = str(request.get("proxy") or "mihomo").strip()
    if proxy not in {"mihomo", "sing-box"}:
        proxy = "mihomo"
    sub_url = str(request.get("subscription_url") or "").strip()
    sub_name = str(request.get("subscription_name") or "主力机场订阅").strip() or "主力机场订阅"
    selected_regions = request.get("regions")
    if not isinstance(selected_regions, list):
        selected_regions = [r["key"] for r in PRESET_REGIONS]
    selected_rules = request.get("rules")
    if not isinstance(selected_rules, list):
        selected_rules = [r["key"] for r in PRESET_RULES]

    urls = [u.strip() for u in re.split(r"[\r\n]+", sub_url) if u.strip()]
    sources = [
        {
            "id": f"s_main_{idx + 1}",
            "name": sub_name if idx == 0 else f"{sub_name} {idx + 1}",
            "url": u,
            "format": "auto",
            "enabled": True,
            "auto_update": True,
            "interval_hours": 6,
        }
        for idx, u in enumerate(urls)
    ]
    nodes = [{
        "id": "n_seed_default",
        "name": "🇭🇰香港 🇹🇼台湾 🇯🇵日本 🇸🇬新加坡 🇺🇸美国 · 示例节点",
        "protocol": "trojan",
        "server": "node.example.com",
        "port": 443,
        "password": "seed-password",
        "tls": {"enabled": True, "server_name": "node.example.com", "insecure": False},
        "udp": True,
        "source": "manual",
        "enabled": True,
    }]

    source_ids = [s["id"] for s in sources]
    node_ids = [n["id"] for n in nodes]

    groups = []
    region_id_by_key = {}
    for reg in PRESET_REGIONS:
        if reg["key"] not in selected_regions:
            continue
        gid = reg["id"]
        region_id_by_key[reg["key"]] = gid
        groups.append({
            "id": gid,
            "name": reg["name"],
            "mode": "url-test",
            "sources": list(source_ids),
            "nodes": list(node_ids),
            "groups": [],
            "include": reg["filter"],
            "exclude": "",
            "interval": 300,
            "enabled": True,
        })

    groups.insert(0, {
        "id": "g_default",
        "name": "🚀 默认代理",
        "mode": "select",
        "sources": list(source_ids),
        "nodes": list(node_ids),
        "groups": list(region_id_by_key.values()),
        "include": "",
        "exclude": "",
        "interval": 300,
        "enabled": True,
    })

    library = []
    policies = []
    preset_lookup = {}
    for p in PRESET_RULES:
        preset_lookup[p["id"]] = p
        preset_lookup[p["key"]] = p
    for item in selected_rules:
        rule_id = item.get("id") or item.get("key") if isinstance(item, dict) else str(item)
        preset = preset_lookup.get(str(rule_id))
        if not preset:
            continue
        lid = "l_" + preset["id"]
        library.append({
            "id": lid,
            "name": preset["name"],
            "kind": "classical",
            "entries": list(preset["entries"]),
            "enabled": True,
        })
        raw_target = (item.get("target") if isinstance(item, dict) else None) or preset["default_target"]
        if raw_target in {"DIRECT", "REJECT"}:
            target = raw_target
        elif raw_target in region_id_by_key:
            target = region_id_by_key[raw_target]
        else:
            target = "g_default"
        policies.append({
            "id": "p_" + preset["id"],
            "name": preset["name"],
            "type": "RULE-SET",
            "value": lid,
            "target": target,
            "enabled": True,
        })

    profile = {
        "schema_version": 1,
        "name": name,
        "sources": sources,
        "nodes": nodes,
        "groups": groups,
        "library": library,
        "policies": policies,
        "fallback": "g_default",
        "settings": {},
    }

    catalog_rev = spaces.snapshot()["revision"]
    created_catalog = spaces.import_profile(
        profile,
        name=name,
        revision=catalog_rev,
        description="通过快速向导一键创建的完整代理方案",
        proxy=proxy,
        rule_output="inline",
    )
    new_space_id = created_catalog["id"]
    spaces.select(new_space_id, spaces.snapshot()["revision"])

    saved_ws = spaces.workspace_snapshot(new_space_id)
    base_url = str(request.get("base_url") or default_base_url).strip()
    link = None
    try:
        link = publications.create(
            new_space_id,
            {
                "proxy": proxy,
                "base_url": base_url,
                "rule_address": "original",
                "revision": saved_ws["revision"],
            },
        )
    except Exception:
        links = publications.listing(new_space_id).get("links", [])
        link = links[-1] if links else None

    runtime = spaces.runtime(new_space_id)
    for sid in source_ids:
        try:
            runtime.subscriptions.refresh(sid)
        except Exception:
            pass

    final_catalog = spaces.snapshot()
    return {
        **final_catalog,
        "spaces": final_catalog,
        "id": new_space_id,
        "subscription": link,
        "subscription_link": link,
        "summary": {
            "nodes": len(profile.get("nodes", [])),
            "groups": len(profile.get("groups", [])),
            "policies": len(profile.get("policies", [])),
        },
    }

