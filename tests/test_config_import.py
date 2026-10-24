"""Tests for importing Mihomo YAML, sing-box JSON, ZIP archives, and LocalDNS configurations."""
from __future__ import annotations

from copy import deepcopy

import base64
import io
import json
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unittest.mock import patch

import yomesh.config_import as config_import_module
from yomesh.compiler import compile_bundle
from yomesh.config_import import MAX_TOTAL_BYTES, fetch_remote_config, import_configuration_space
from yomesh.model import ConfigError
from yomesh.server import handler_for
from yomesh.spaces import ConfigurationSpaces


MIHOMO_PORTABLE_YAML = """
mixed-port: 7890
dns:
  enable: true
  listen: 0.0.0.0:1053
proxies:
  - name: HK-Node-1
    type: ss
    server: 198.51.100.10
    port: 8388
    cipher: aes-128-gcm
    password: secretpassword
  - name: JP-Node-1
    type: vless
    server: 198.51.100.20
    port: 443
    uuid: 11111111-2222-3333-4444-555555555555
    tls: true
    servername: example.com
proxy-groups:
  - name: Proxy
    type: select
    proxies:
      - Auto
      - HK-Node-1
      - JP-Node-1
      - DIRECT
  - name: Auto
    type: url-test
    proxies:
      - HK-Node-1
      - JP-Node-1
    url: https://www.gstatic.com/generate_204
    interval: 300
rules:
  - DOMAIN-SUFFIX,google.com,Proxy
  - DOMAIN-KEYWORD,github,Proxy
  - IP-CIDR,1.1.1.1/32,Proxy,no-resolve
  - MATCH,DIRECT
"""

SINGBOX_SAMPLE_JSON = {
    "inbounds": [
        {
            "type": "mixed",
            "tag": "mixed-in",
            "listen": "127.0.0.1",
            "listen_port": 7892,
        }
    ],
    "outbounds": [
        {
            "type": "selector",
            "tag": "Proxy",
            "outbounds": ["Auto", "SG-Hysteria2", "DIRECT"],
        },
        {
            "type": "urltest",
            "tag": "Auto",
            "outbounds": ["SG-Hysteria2"],
            "url": "https://www.gstatic.com/generate_204",
            "interval": "5m",
        },
        {
            "type": "hysteria2",
            "tag": "SG-Hysteria2",
            "server": "203.0.113.8",
            "server_port": 8443,
            "password": "hy2-password",
            "tls": {
                "enabled": True,
                "server_name": "sg.example.com",
            },
        },
        {"type": "direct", "tag": "DIRECT"},
    ],
    "route": {
        "rule_set": [
            {
                "tag": "ai_rules",
                "type": "remote",
                "format": "binary",
                "url": "https://example.com/rules/ai.srs",
            }
        ],
        "rules": [
            {"action": "sniff"},
            {"protocol": "dns", "action": "hijack-dns"},
            {"rule_set": ["ai_rules"], "outbound": "Proxy"},
            {
                "domain_suffix": ["openai.com", "anthropic.com"],
                "outbound": "Proxy",
            },
            {"ip_cidr": ["8.8.8.8/32"], "outbound": "DIRECT"},
        ],
        "final": "Proxy",
    },
}


class ConfigImportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "web").mkdir(parents=True, exist_ok=True)
        (self.root / "examples").mkdir(parents=True, exist_ok=True)
        (self.root / "examples" / "profile.json").write_text(
            (ROOT / "examples" / "profile.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temporary.cleanup()

    def test_import_mihomo_portable_and_dual_core_compile(self):
        spaces = ConfigurationSpaces(self.root)
        try:
            revision = spaces.snapshot()["revision"]
            result = import_configuration_space(
                spaces,
                {
                    "name": "Mihomo 通用导入",
                    "filename": "mihomo.yaml",
                    "content": MIHOMO_PORTABLE_YAML.replace("      - DIRECT\n", ""),
                    "mode": "portable",
                    "draft": True,
                    "revision": revision,
                },
                ROOT,
            )
            space_id = result["id"]
            self.assertNotEqual(space_id, "default")
            self.assertEqual(result["import_summary"]["mode"], "portable")
            self.assertEqual(result["import_summary"]["nodes"], 2)
            self.assertEqual(result["import_summary"]["groups"], 2)

            profile = spaces.workspace_snapshot(space_id)["profile"]
            mihomo_bundle = compile_bundle(profile, "mihomo", rule_output="inline")
            singbox_bundle = compile_bundle(profile, "sing-box", rule_output="inline")
            self.assertIn("mihomo.yaml", mihomo_bundle["files"])
            self.assertIn("sing-box.json", singbox_bundle["files"])
        finally:
            spaces.close()

    def test_import_singbox_json_with_localdns_zip_wiring(self):
        spaces = ConfigurationSpaces(self.root)
        try:
            revision = spaces.snapshot()["revision"]
            localdns_zip_buf = io.BytesIO()
            with zipfile.ZipFile(localdns_zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(
                    "localdns/config/localdns.json",
                    json.dumps({"dns": {"udp_listen": "127.0.0.1:5353", "tcp_listen": "127.0.0.1:5353"}}),
                )
                zf.writestr(
                    "localdns/config/settings/overrides.json",
                    json.dumps({"socks5": "127.0.0.1:7899", "ecs": "1.2.4.0/24"}),
                )
                zf.writestr("localdns/config/upstreams/foreign.json", json.dumps({"upstreams": [{"addr": "tcp://1.1.1.1", "enabled": True}]}))
            localdns_b64 = base64.b64encode(localdns_zip_buf.getvalue()).decode("ascii")
            sample = deepcopy(SINGBOX_SAMPLE_JSON)
            sample["outbounds"][0]["outbounds"].remove("DIRECT")
            sample["route"]["rules"] = sample["route"]["rules"][2:]
            sample["route"]["rule_set"] = [{"tag": "ai_rules", "type": "inline", "rules": [{"domain_suffix": ["example.test"]}]}]

            result = import_configuration_space(
                spaces,
                {
                    "name": "sing-box + LocalDNS",
                    "filename": "sing-box.json",
                    "content": json.dumps(sample),
                    "localdns_filename": "localdns.zip",
                    "localdns_base64": localdns_b64,
                    "mode": "auto",
                    "draft": True,
                    "revision": revision,
                },
                ROOT,
            )
            space_id = result["id"]
            self.assertEqual(result["import_summary"]["format"], "sing-box")
            self.assertTrue(result["import_summary"]["localdns_attached"])

            profile = spaces.workspace_snapshot(space_id)["profile"]
            self.assertEqual(profile["settings"]["mixed_port"], 7899)
            self.assertEqual(profile["settings"]["dns_port"], 5353)
            self.assertEqual(profile["settings"]["dns_socks5"], "127.0.0.1:7899")
            self.assertFalse(profile["mosdns"]["migration"]["reviewed"])
            # The test explicitly reviews the draft before combined export.
            profile["mosdns"]["migration"]["reviewed"] = True

            sb_bundle = compile_bundle(profile, "sing-box", rule_output="inline")
            mh_bundle = compile_bundle(profile, "mihomo", rule_output="inline")
            self.assertIn("sing-box.json", sb_bundle["files"])
            self.assertIn("mihomo.yaml", mh_bundle["files"])
        finally:
            spaces.close()

    def test_import_mihomo_native_zip_via_http_api(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        handler = handler_for(self.root, server.server_port)

        class Quiet(handler):
            def log_message(self, *args):
                pass

        server.RequestHandlerClass = Quiet
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with urlopen(Request(base + "/api/spaces"), timeout=10) as resp:
                catalog = json.loads(resp.read())

            zip_buf = io.BytesIO()
            native_yaml = yaml.safe_load(MIHOMO_PORTABLE_YAML)
            native_yaml["external-ui"] = "/etc/mihomo/ui"
            native_yaml["rule-providers"] = {
                "direct_list": {
                    "type": "file",
                    "behavior": "domain",
                    "format": "yaml",
                    "path": "ruleset/direct.yaml",
                }
            }
            native_yaml["rules"].insert(0, "RULE-SET,direct_list,DIRECT")
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("config.yaml", yaml.safe_dump(native_yaml, allow_unicode=True))
                zf.writestr("ruleset/direct.yaml", "payload:\n  - '+.cn'\n")
                zf.writestr(
                    "localdns/config/localdns.json",
                    json.dumps({"dns": {"listen": ["0.0.0.0:1053"]}}),
                )

            req_body = {
                "name": "网关原生方案",
                "filename": "gateway.zip",
                "content_base64": base64.b64encode(zip_buf.getvalue()).decode("ascii"),
                "mode": "auto",
                "draft": True,
                "revision": catalog["revision"],
            }
            req = Request(
                base + "/api/spaces/import-config",
                data=json.dumps(req_body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urlopen(req, timeout=15) as resp:
                self.assertEqual(resp.status, 200)
                imported = json.loads(resp.read())

            self.assertEqual(imported["import_summary"]["mode"], "native")
            self.assertTrue(imported["import_summary"]["localdns_attached"])
            space_entry = next(s for s in imported["spaces"] if s["id"] == imported["id"])
            self.assertTrue(space_entry["native"])
            self.assertEqual(space_entry["dns_engine"], "localdns")
            saved_profile = handler.space_catalog.workspace_snapshot(imported["id"])["profile"]
            self.assertEqual(saved_profile["native_configuration"]["mihomo"]["external-ui"], "ui")
        finally:
            server.shutdown()
            server.server_close()
            handler.space_catalog.close()


class _FakeRemoteResponse:
    def __init__(self, body: bytes, url: str):
        self._body = body
        self.url = url

    def read(self, limit: int = -1) -> bytes:
        return self._body if limit is None or limit < 0 else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FetchRemoteConfigTests(unittest.TestCase):
    """成功路径通过替换 DNS 解析与 opener 验证，不依赖本机网络环境。"""

    # 解析结果固定为公网地址，避免不同网络对 example.com 的解析差异影响测试。
    PUBLIC_ADDR_INFO = [(2, 1, 6, "", ("93.184.216.34", 443))]

    def fetch(self, url: str, body: bytes, final_url: str | None = None):
        captured = {}

        def fake_open(request, timeout=None):
            captured["ua"] = request.headers.get("User-agent")
            captured["url"] = request.full_url
            return _FakeRemoteResponse(body, final_url or request.full_url)

        with patch.object(config_import_module, "socket") as fake_socket, patch.object(config_import_module._REMOTE_OPENER, "open", new=fake_open):
            fake_socket.getaddrinfo.return_value = self.PUBLIC_ADDR_INFO
            result = fetch_remote_config({"url": url})
        return result, captured

    def test_fetch_text_yaml_and_user_agent(self):
        body = "mixed-port: 7890\nrules:\n  - MATCH,DIRECT\n".encode("utf-8")
        result, captured = self.fetch("https://example.com/templates/gateway.yaml", body)
        self.assertEqual(result["filename"], "gateway.yaml")
        self.assertEqual(result["text"], body.decode("utf-8"))
        self.assertEqual(result["size"], len(body))
        self.assertEqual(captured["ua"], "clash.meta")
        self.assertEqual(captured["url"], "https://example.com/templates/gateway.yaml")

    def test_fetch_infers_yaml_extension(self):
        result, _ = self.fetch("https://example.com/download/config", b"rules: []\n")
        self.assertEqual(result["filename"], "config.yaml")
        result, _ = self.fetch("https://example.com", b"rules: []\n")
        self.assertEqual(result["filename"], "remote-config.yaml")

    def test_fetch_rejects_zip_and_binary_payloads(self):
        with self.assertRaisesRegex(ConfigError, "配置包"):
            self.fetch("https://example.com/pkg.zip", b"PK\x03\x04rest-of-archive")
        with self.assertRaisesRegex(ConfigError, "UTF-8"):
            self.fetch("https://example.com/blob.yaml", b"\xff\xfe\x00\x01")

    def test_fetch_rejects_oversize_payload(self):
        with self.assertRaisesRegex(ConfigError, "16 MiB"):
            self.fetch("https://example.com/big.yaml", b"x" * (MAX_TOTAL_BYTES + 1))

    def test_fetch_maps_http_error(self):
        def raise_404(request, timeout=None):
            raise HTTPError(request.full_url, 404, "Not Found", None, None)

        with patch.object(config_import_module, "socket") as fake_socket, patch.object(config_import_module._REMOTE_OPENER, "open", new=raise_404):
            fake_socket.getaddrinfo.return_value = self.PUBLIC_ADDR_INFO
            with self.assertRaisesRegex(ConfigError, "HTTP 404"):
                fetch_remote_config({"url": "https://example.com/missing.yaml"})


class RemoteTargetGuardTests(unittest.TestCase):
    """内网 / 环回 / 链路本地目标在校验层直接拒绝，不发起到这些地址的请求。"""

    def test_guard_rejects_non_public_targets(self):
        from yomesh.config_import import _assert_public_http_target

        cases = [
            "ftp://example.com/config.yaml",
            "http://user:pass@example.com/config.yaml",
            "http://127.0.0.1:8767/config.yaml",
            "http://localhost/config.yaml",
            "http://192.168.1.10/config.yaml",
            "https://169.254.169.254/latest/meta-data",
        ]
        for url in cases:
            with self.subTest(url=url):
                with self.assertRaises(ConfigError):
                    _assert_public_http_target(url)

    def test_fetch_rejects_missing_url(self):
        with self.assertRaisesRegex(ConfigError, "地址无效"):
            fetch_remote_config({"url": ""})

    def test_guard_allows_fake_ip_dns_resolution(self):
        from yomesh.config_import import _assert_public_http_target

        # 开启 Fake-IP DNS 的设备把公网域名解析到 198.18.0.0/15，应放行。
        _assert_public_http_target("http://198.18.27.47/config.yaml")


if __name__ == "__main__":
    unittest.main()
