"""Static UI and native bundle export, with explicit host/origin allowlisting."""
from __future__ import annotations

import io
import json
import re
import zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from .auth import SESSION_COOKIE, SESSION_TTL_SECONDS, AuthStore, AuthUnavailable
from .compiler import compile_bundle
from .hermes_access import HermesAccess, HermesUnavailable
from .hermes_service import HermesService
from .model import ConfigError, Profile
from .native import find_core, serialize_config, validate_bundle
from .publication import Publications
from .quick_setup import ClientControllers, PRESET_REGIONS, PRESET_RULES, quick_add_region_groups, quick_create_space
from .spaces import ConfigurationSpaces
from .workspace import Conflict


def handler_for(root: Path, port: int):
    spaces = ConfigurationSpaces(root)
    publications = Publications(root, spaces)
    hermes_access = HermesAccess(root)
    auth = AuthStore(root)
    controllers = ClientControllers(root)
    from .interaction_service import InteractionService
    interactions = InteractionService(root, spaces)
    hermes_service = HermesService(root, spaces, publications, interactions, controllers=controllers)
    subscriptions = spaces.runtime(spaces.state["active"]).subscriptions

    class Handler(SimpleHTTPRequestHandler):
        subscription_service = subscriptions
        space_catalog = spaces
        hermes_store = hermes_access
        auth_store = auth
        controller_store = controllers
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(root / "web"), **kwargs)

        def end_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def json_response(self, status, value, extra_headers=()):
            if status == 200 and isinstance(value, dict):
                scope = self.headers.get("X-YoMesh-Space")
                if "profile" in value and "revision" in value:
                    value = spaces.decorate(value, scope)
                elif isinstance(value.get("workspace"), dict):
                    value = {**value, "workspace": spaces.decorate(value["workspace"], scope)}
            raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            for hk, hv in extra_headers:
                self.send_header(hk, hv)
            self.end_headers()
            self.wfile.write(raw)

        def authenticated_user(self) -> str | None:
            if not auth.is_enabled():
                return auth.current_username()
            return auth.verify_cookie(self.headers.get("Cookie"))

        def log_message(self, format, *args):
            if self.path.startswith("/s/"):
                return super().log_message("%s", "subscription request (URL redacted)")
            if "?" in self.path:
                # Credentials can occur in query strings, including legacy MCP URLs.
                return super().log_message("%s", self.path.split("?", 1)[0] + " (query redacted)")
            return super().log_message(format, *args)

        def send_head(self):
            # Guard the HTML itself, including HEAD, query strings and encoded
            # aliases. No workspace shell is delivered before authentication.
            target = Path(self.translate_path(self.path)).resolve()
            web = (root / "web").resolve()
            if target in {web, web / "index.html"}:
                try:
                    user = self.authenticated_user()
                except AuthUnavailable:
                    user = None
                if user is None:
                    self.send_response(302)
                    self.send_header("Location", "/login.html")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return None
            return super().send_head()

        def do_GET(self):
            try:
                self._do_GET()
            except AuthUnavailable as error:
                self.json_response(503, {"error": str(error), "auth_unavailable": True})
            except HermesUnavailable as error:
                self.json_response(503, {"error": str(error)})

        def _do_GET(self):
            if self.path.startswith("/s/"):
                match = re.fullmatch(r"/s/([A-Za-z0-9_-]{43})/(?:(mihomo\.yaml|sing-box\.json)|assets/([a-f0-9]{24}))", self.path)
                try:
                    result = publications.read(match[1], asset=match[3], filename=match[2]) if match else None
                    if result is None:
                        self.json_response(404, {"error": "订阅链接不存在或已停用"})
                        return
                    raw, kind, source = result
                    self.send_response(200)
                    self.send_header("Content-Type", kind)
                    self.send_header("Content-Length", str(len(raw)))
                    self.send_header("X-YoMesh-Content-Source", source)
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.end_headers()
                    self.wfile.write(raw)
                except ConfigError as error:
                    self.json_response(422, {"error": str(error)})
                except (OSError, ValueError, TypeError):
                    self.json_response(503, {"error": "配置生成未完成，请稍后重试或在工作台检查配置"})
                return
            if self.path == "/api/health":
                found = {}
                for core in ("mihomo", "sing-box", "mosdns", "localdns"):
                    try:
                        find_core(root, core)
                        found[core] = "installed"
                    except ConfigError:
                        found[core] = "missing"
                self.json_response(200, {"app": "YoMesh", "version": __version__, "cores": found, "workspace": "persistent"})
                return
            if self.path == "/api/auth/status":
                user = self.authenticated_user()
                self.json_response(200, {"enabled": auth.is_enabled(), "authenticated": user is not None, "username": user or auth.current_username()})
                return
            if self.path.startswith("/api/setup/"):
                name = self.path.removeprefix("/api/setup/")
                if name not in {"yomesh_hermes_mcp.py", "hermes_installer.py"}:
                    self.json_response(404, {"error": "安装文件不存在"})
                    return
                try:
                    raw = (root / "scripts" / name).read_bytes()
                except OSError:
                    self.json_response(503, {"error": "安装文件暂不可用，请检查 YoMesh 安装目录中的 scripts 文件夹"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/x-python; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{name}"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            if self.path.startswith("/api/hermes/"):
                if not hermes_access.verify(self.headers.get("Authorization")):
                    self.json_response(401, {"error": "Hermes 接入密钥无效或已停用"})
                    return
                try:
                    if self.path == "/api/hermes/spaces":
                        self.json_response(200, hermes_service.spaces_overview())
                    else:
                        self.json_response(404, {"error": "Hermes 接口不存在"})
                except ConfigError as error:
                    self.json_response(422, {"error": str(error)})
                return
            if self.path.startswith("/api/") and self.authenticated_user() is None:
                self.json_response(401, {"error": "请先登录 YoMesh 工作台", "auth_required": True, "username": auth.current_username()})
                return
            if self.path == "/api/integrations/hermes":
                self.json_response(200, hermes_access.listing())
                return
            if self.path == "/api/spaces/presets":
                links_by_space = {
                    item["id"]: publications.listing(item["id"]).get("links", [])
                    for item in spaces.snapshot().get("spaces", [])
                }
                self.json_response(200, {
                    "regions": PRESET_REGIONS,
                    "rules": PRESET_RULES,
                    "controllers": controllers.all_items(),
                    "links_by_space": links_by_space,
                })
                return
            if self.path == "/api/spaces":
                self.json_response(200, spaces.snapshot())
                return
            if self.path.startswith("/api/"):
                try:
                    workspace = spaces.runtime(self.headers.get("X-YoMesh-Space")).workspace
                except ConfigError as error:
                    self.json_response(422, {"error": str(error)})
                    return
            if self.path == "/api/example":
                self.json_response(200, json.loads((root / "examples/profile.json").read_text(encoding="utf-8")))
            elif self.path == "/api/workspace":
                self.json_response(200, workspace.snapshot())
            elif self.path == "/api/workspace/subscriptions":
                self.json_response(200, publications.listing(self.headers.get("X-YoMesh-Space")))
            elif self.path == "/api/workspace/revision":
                self.json_response(200, {"revision": workspace.revision()})
            elif self.path == "/api/workspace/mosdns":
                from .mosdns import view
                self.json_response(200, view(spaces.workspace_snapshot(self.headers.get("X-YoMesh-Space"))["profile"]))
            elif self.path == "/api/workspace/base-configs":
                from .base_config import base_editor
                profile = spaces.workspace_snapshot(self.headers.get("X-YoMesh-Space"))["profile"]
                self.json_response(200, base_editor(Profile({"schema_version": 1, "fallback": "DIRECT", "settings": profile.get("settings", {}), "base_configs": profile.get("base_configs", {})}), profile.get("native_configuration")))
            else:
                super().do_GET()

        def do_POST(self):
            if self.path != "/api/export" and not self.path.startswith(("/mcp", "/api/auth", "/api/spaces", "/api/hermes", "/api/integrations/hermes", "/api/sources", "/api/workspace", "/api/nodes", "/api/library", "/api/groups")):
                self.json_response(404, {"error": "接口不存在"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 24 * 1024 * 1024:
                    self.json_response(413, {"error": "配置需要为 1 字节至 24 MiB"})
                    return
                request = json.loads(self.rfile.read(length).decode("utf-8"))
                if not isinstance(request, dict):
                    raise ConfigError("请求需要对象")
                if self.path.startswith("/mcp"):
                    from urllib.parse import parse_qs, urlsplit
                    auth_header = self.headers.get("Authorization")
                    if not auth_header:
                        q_token = (parse_qs(urlsplit(self.path).query).get("token") or [""])[0].strip()
                        if q_token:
                            auth_header = f"Bearer {q_token}"
                    if not hermes_access.verify(auth_header):
                        self.json_response(401, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32001, "message": "Hermes 接入密钥无效或已停用"}})
                        return
                    host = self.headers.get("Host") or f"127.0.0.1:{port}"
                    rpc_resp = hermes_service.handle_mcp_rpc(request, f"http://{host}")
                    if rpc_resp is None:
                        self.json_response(202, {"ok": True})
                    else:
                        self.json_response(200, rpc_resp)
                    return
                if self.path.startswith("/api/hermes/"):
                    if not hermes_access.verify(self.headers.get("Authorization")):
                        self.json_response(401, {"error": "Hermes 接入密钥无效或已停用"})
                        return
                    if self.path == "/api/hermes/diagnose":
                        self.json_response(200, hermes_service.diagnose_query(request))
                    elif self.path == "/api/hermes/rules/add":
                        self.json_response(200, hermes_service.add_rule(request))
                    elif self.path == "/api/hermes/nodes":
                        self.json_response(200, hermes_service.nodes_overview(request))
                    elif self.path == "/api/hermes/subscription":
                        host = self.headers.get("Host") or f"127.0.0.1:{port}"
                        self.json_response(200, hermes_service.subscription_link(request, f"http://{host}"))
                    else:
                        self.json_response(404, {"error": "Hermes 接口不存在"})
                    return
                if self.path == "/api/auth/login":
                    token = auth.login(str(request.get("username") or ""), str(request.get("password") or ""))
                    cookie_val = f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax"
                    self.json_response(200, {"ok": True, "username": auth.current_username(), "enabled": True}, extra_headers=[("Set-Cookie", cookie_val)])
                    return
                if self.path == "/api/auth/logout":
                    cookie_val = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                    self.json_response(200, {"ok": True}, extra_headers=[("Set-Cookie", cookie_val)])
                    return
                if self.authenticated_user() is None:
                    self.json_response(401, {"error": "请先登录 YoMesh 工作台", "auth_required": True, "username": auth.current_username()})
                    return
                if self.path == "/api/auth/update":
                    if request.get("action") == "disable":
                        auth.disable_auth(str(request.get("current_password") or ""))
                        cookie_val = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"
                        self.json_response(200, {"ok": True, "username": auth.current_username(), "enabled": False}, extra_headers=[("Set-Cookie", cookie_val)])
                        return
                    token = auth.update_credentials(
                        str(request.get("current_password") or ""),
                        str(request.get("username") or ""),
                        str(request.get("new_password") or ""),
                    )
                    cookie_val = f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_TTL_SECONDS}; HttpOnly; SameSite=Lax"
                    self.json_response(200, {"ok": True, "username": auth.current_username(), "enabled": True}, extra_headers=[("Set-Cookie", cookie_val)])
                    return
                if self.path.startswith("/api/integrations/hermes"):
                    if self.path == "/api/integrations/hermes/rotate":
                        from .hermes_setup import hermes_setup_metadata, installation_command
                        from .publication import public_base
                        base = public_base(request.get("base_url") or "http://" + self.headers.get("Host", "127.0.0.1"))
                        setup = hermes_setup_metadata(root)
                        rotated = hermes_access.rotate()
                        self.json_response(200, {**rotated, "installation_command": installation_command(base, rotated["token"], setup)})
                    elif self.path == "/api/integrations/hermes/revoke":
                        self.json_response(200, hermes_access.revoke())
                    else:
                        self.json_response(404, {"error": "Hermes 接口不存在"})
                    return
                if self.path == "/api/spaces/quick-create":
                    host = self.headers.get("Host") or f"127.0.0.1:{port}"
                    self.json_response(200, quick_create_space(spaces, publications, request, f"http://{host}"))
                    return
                if self.path == "/api/spaces/client-controller":
                    target_space = str(request.get("space_id") or self.headers.get("X-YoMesh-Space") or spaces.state["active"])
                    if request.get("action") == "reload":
                        self.json_response(200, controllers.notify_reload(target_space))
                    else:
                        self.json_response(200, controllers.update(target_space, request))
                    return
                if self.path == "/api/spaces/group-category-names":
                    self.json_response(200, spaces.save_group_category_names(request.get("names"), request.get("revision")))
                    return
                if self.path == "/api/spaces/import-config":
                    from .config_import import import_configuration_space
                    self.json_response(200, import_configuration_space(spaces, request, root))
                    return
                if self.path == "/api/spaces/fetch-config":
                    from .config_import import fetch_remote_config
                    self.json_response(200, fetch_remote_config(request))
                    return
                if self.path == "/api/spaces/import-native":
                    profile = {"schema_version": 1, "fallback": "DIRECT", "native_configuration": request.get("configuration")}
                    # Explicit draft import preserves faulty originals for comparison.
                    # This never bypasses the export compiler or native download gate.
                    Profile(profile)
                    if request.get("draft") is not True:
                        validate_bundle(compile_bundle(profile, "mihomo", rule_output="remote"), root)
                    self.json_response(200, spaces.import_native(request.get("configuration"), request.get("name"), request.get("revision"), request.get("description", ""), mosdns=request.get("mosdns")))
                    return
                if self.path in {"/api/spaces/save", "/api/spaces/select", "/api/spaces/delete"}:
                    if self.path.endswith("/save"):
                        result = spaces.save(request.get("space"), request.get("revision"), request.get("id"), request.get("copy_from"), request.get("workspace_revision"))
                    elif self.path.endswith("/select"):
                        result = spaces.select(request.get("id"), request.get("revision"))
                    else:
                        result = spaces.delete(request.get("id"), request.get("revision"))
                    self.json_response(200, result)
                    return
                runtime = spaces.runtime(self.headers.get("X-YoMesh-Space"))
                workspace, library, subscriptions = runtime.workspace, runtime.library, runtime.subscriptions
                scope = self.headers.get("X-YoMesh-Space")
                if self.path == "/api/workspace/quick-region-groups":
                    active_scope = scope or spaces.state["active"]
                    result = quick_add_region_groups(spaces, active_scope, request.get("regions"))
                    reload_status = controllers.notify_reload(active_scope)
                    self.json_response(200, {**result, "client_reload": reload_status})
                    return
                if self.path == "/api/workspace/tools":
                    self.json_response(200, interactions.handle(scope or spaces.state["active"], request))
                    return
                if self.path == "/api/workspace/subscriptions":
                    self.json_response(200, publications.create(scope, request))
                    return
                if self.path == "/api/workspace/subscriptions/revoke":
                    self.json_response(200, publications.revoke(scope, request.get("id")))
                    return
                if self.path == "/api/workspace/native-listener":
                    from .native_listeners import change_listener
                    snapshot = spaces.workspace_snapshot(scope)
                    native = snapshot["profile"].get("native_configuration")
                    if native is None:
                        raise ConfigError("当前空间不是原生配置空间")
                    workspace._revision(request.get("revision"))
                    snapshot["profile"]["native_configuration"] = change_listener(native, request)
                    self.json_response(200, spaces.save_profile(snapshot["profile"], request.get("revision"), scope))
                    return
                if self.path == "/api/workspace/export-check":
                    snapshot = spaces.workspace_snapshot(scope) if request.get("use_workspace") else None
                    if snapshot is not None:
                        workspace._revision(request.get("revision"))
                    profile = snapshot["profile"] if snapshot is not None else request.get("profile")
                    try:
                        bundle = compile_bundle(profile, request.get("proxy", "mihomo"), request.get("group_mode_overrides"), request.get("rule_output", "inline"), component=request.get("component", "proxy"))
                        result = {"ready": True, "file_count": len(bundle["files"]), "native_validation": "pending"}
                    except ConfigError as error:
                        result = {"ready": False, "error": str(error), "native_validation": "pending"}
                    self.json_response(200, result)
                    return
                if self.path in {"/api/workspace/native-policy", "/api/workspace/native-policy-preview"}:
                    from .native_policy import change, prepare_request, row_view
                    snapshot = spaces.workspace_snapshot(scope)
                    native = snapshot["profile"].get("native_configuration")
                    if native is None:
                        raise ConfigError("当前空间不是原生配置空间")
                    workspace._revision(request.get("revision"))
                    if self.path.endswith("-preview"):
                        if request.get("unit") is True:
                            from .native_rule_groups import preview
                            self.json_response(200, preview(native, request))
                            return
                        prepared, row = prepare_request(native, request, fetch=False)
                        self.json_response(200, {"policy": row_view(prepared, row), "text": row["text"]})
                    else:
                        snapshot["profile"]["native_configuration"] = change(native, request)
                        saved = spaces.save_profile(snapshot["profile"], request.get("revision"), scope)
                        controllers.notify_reload(scope or spaces.state["active"])
                        self.json_response(200, saved)
                    return
                if self.path == "/api/workspace/native":
                    snapshot = spaces.workspace_snapshot(scope)
                    if "native_configuration" not in snapshot["profile"]:
                        raise ConfigError("当前空间不是原生配置空间")
                    snapshot["profile"]["native_configuration"] = request.get("configuration")
                    saved = spaces.save_profile(snapshot["profile"], request.get("revision"), scope, request.get("share_scope", "current"))
                    controllers.notify_reload(scope or spaces.state["active"])
                    self.json_response(200, saved)
                    return
                if self.path == "/api/workspace/native-refresh":
                    from .native_configuration import refresh_provider
                    snapshot = spaces.workspace_snapshot(scope)
                    workspace._revision(request.get("revision"))
                    snapshot["profile"]["native_configuration"] = refresh_provider(snapshot["profile"].get("native_configuration"), request.get("section"), request.get("name"))
                    saved = spaces.save_profile(snapshot["profile"], request.get("revision"), scope, "shared")
                    controllers.notify_reload(scope or spaces.state["active"])
                    self.json_response(200, saved)
                    return
                if self.path == "/api/workspace/preview-selection":
                    self.json_response(200, spaces.preview_selection(request.get("selection"), request.get("revision"), scope, request.get("blank") is True))
                    return
                if self.path == "/api/workspace/selection":
                    self.json_response(200, spaces.save_selection(request.get("selection"), request.get("revision"), scope))
                    return
                if self.path == "/api/workspace/import-legacy":
                    values = request.get("sources", [])
                    if not isinstance(values, list):
                        raise ConfigError("订阅需要数组")
                    self.json_response(200, workspace.import_legacy(values))
                    return
                if self.path == "/api/workspace":
                    saved = spaces.save_profile(request.get("profile"), request.get("revision"), scope)
                    controllers.notify_reload(scope or spaces.state["active"])
                    self.json_response(200, saved)
                    return
                if self.path == "/api/workspace/import":
                    self.json_response(200, workspace.import_configuration(request.get("configuration"), request.get("revision")))
                    return
                if self.path == "/api/workspace/select-plan":
                    self.json_response(200, workspace.switch_plan(request.get("id"), request.get("revision")))
                    return
                if self.path == "/api/workspace/reorder-policies":
                    saved = workspace.reorder_policies(request.get("ids"), request.get("plan"), request.get("revision"))
                    controllers.notify_reload(scope or spaces.state["active"])
                    self.json_response(200, saved)
                    return
                if self.path == "/api/workspace/settings":
                    self.json_response(200, spaces.save_settings(request.get("settings"), request.get("revision"), scope))
                    return
                if self.path in {"/api/workspace/mosdns/import", "/api/workspace/mosdns/import-localdns", "/api/workspace/mosdns/save", "/api/workspace/mosdns/preview", "/api/workspace/mosdns/check", "/api/workspace/mosdns/refresh"}:
                    from .mosdns import checked, refresh_ip_source, refresh_rule
                    workspace._revision(request.get("revision"))
                    profile = spaces.workspace_snapshot(scope)["profile"]
                    if self.path.endswith("/import-localdns"):
                        from .mosdns_import import import_localdns
                        self.json_response(200, import_localdns(profile, root))
                        return
                    if self.path.endswith("/import"):
                        self.json_response(200, {"settings": checked(request.get("config"), profile.get("settings")), "note": "已读取到草稿，尚未保存"})
                        return
                    if self.path.endswith("/refresh"):
                        from copy import deepcopy
                        profile["mosdns"] = deepcopy(request.get("config"))
                        if not isinstance(profile["mosdns"], dict) or not isinstance(profile["mosdns"].get("rules"), list):
                            raise ConfigError("MosDNS 配置需要规则列表")
                        ip_source = request.get("kind") == "ip"
                        items = profile["mosdns"].get("fallback", {}).get("sources", []) if ip_source else profile["mosdns"]["rules"]
                        rule = next((r for r in items if r["id"] == request.get("id")), None)
                        if not rule:
                            raise ConfigError("DNS 规则不存在")
                        updated = refresh_ip_source(root, rule) if ip_source else refresh_rule(root, rule)
                        items[items.index(rule)] = updated
                        profile["mosdns"] = checked(profile["mosdns"])
                        self.json_response(200, {"config": profile["mosdns"], "note": "来源已读取到草稿；保存后参与导出"})
                        return
                    profile["mosdns"] = checked(request.get("config"), profile.get("settings"))
                    if self.path.endswith("/save"):
                        self.json_response(200, spaces.save_mosdns(profile["mosdns"], request.get("revision"), scope))
                    else:
                        bundle = compile_bundle(profile, rule_output="inline", component="dns", dns_engine="mosdns")
                        if self.path.endswith("/check"):
                            validate_bundle(bundle, root)
                        self.json_response(200, {"config": bundle["files"]["mosdns.yaml"], "settings": profile["mosdns"], "validation": bundle["manifest"]["validation"]})
                    return
                if self.path == "/api/workspace/base-config":
                    self.json_response(200, spaces.save_base_config(request.get("target"), request.get("text"), request.get("revision"), scope))
                    return
                if self.path in {"/api/workspace/preview-base-config", "/api/workspace/check-base-config"}:
                    from .base_config import prepare_base_profile
                    workspace._revision(request.get("revision"))
                    profile = prepare_base_profile(spaces.workspace_snapshot(scope)["profile"], request.get("target"), request.get("text"))
                    bundle = compile_bundle(profile, request.get("target"), request.get("group_mode_overrides"), "remote" if "native_configuration" in profile else "inline", component="proxy")
                    if self.path.endswith("check-base-config"):
                        validate_bundle(bundle, root)
                    self.json_response(200, {"files": {name: serialize_config(config) for name, config in bundle["files"].items() if not isinstance(config, bytes)}, "validation": bundle["manifest"]["validation"]})
                    return
                if self.path == "/api/workspace/ensure-plan":
                    self.json_response(200, workspace.ensure_plan(request.get("revision")))
                    return
                if self.path == "/api/library/prepare-editor":
                    item = next((i for i in workspace.snapshot()["profile"]["library"] if i["id"] == request.get("id")), None)
                    if not item:
                        raise ConfigError("规则不存在")
                    parsed = Profile({"schema_version": 1, "fallback": "DIRECT", "library": [item]})
                    self.json_response(200, {"criteria": parsed.criteria_cache[item["id"]]})
                    return
                if self.path == "/api/library/save-batch":
                    workspace._revision(request.get("revision"))
                    values = request.get("items")
                    if not isinstance(values, list) or not 1 <= len(values) <= 16:
                        raise ConfigError("一次可添加 1–16 条规则")
                    prepared = [library.prepare(item) for item in values]
                    saved = workspace.save_libraries([i for i, _ in prepared], [s for _, s in prepared], request.get("revision"))
                    controllers.notify_reload(scope or spaces.state["active"])
                    self.json_response(200, saved)
                    return
                if self.path == "/api/workspace/preview-dns":
                    self.json_response(200, workspace.preview_dns(request.get("settings", {}), spaces.workspace_snapshot(scope)["profile"]))
                    return
                if self.path == "/api/workspace/preview-export":
                    profile = spaces.workspace_snapshot(scope)["profile"] if request.get("use_workspace") else request.get("profile")
                    bundle = compile_bundle(profile, request.get("proxy", "mihomo"), request.get("group_mode_overrides"), request.get("rule_output", "inline"), request.get("component", "both"))
                    self.json_response(200, {"files": {name: ("二进制资产 · " + str(len(config)) + " 字节") if isinstance(config, bytes) else serialize_config(config) for name, config in bundle["files"].items()}, "manifest": bundle["manifest"]})
                    return
                if self.path == "/api/workspace/check-profile":
                    bundle = compile_bundle(request.get("profile"), request.get("proxy", "mihomo"), request.get("group_mode_overrides"), request.get("rule_output", "inline"), request.get("component", "both"))
                    validate_bundle(bundle, root)
                    self.json_response(200, {"validation": bundle["manifest"]["validation"]})
                    return
                if self.path == "/api/workspace/check":
                    profile = spaces.workspace_snapshot(scope)["profile"]
                    bundle = compile_bundle(profile, request.get("proxy", "mihomo"), request.get("group_mode_overrides"), request.get("rule_output", "inline"), request.get("component", "both"))
                    validate_bundle(bundle, root)
                    self.json_response(200, {"validation": bundle["manifest"]["validation"], "usage": Profile.routing_usage(profile)})
                    return
                if self.path == "/api/workspace/preview-group":
                    self.json_response(200, workspace.preview_group(request.get("profile", {}), request.get("group_id")))
                    return
                if self.path == "/api/workspace/preview-rule":
                    self.json_response(200, workspace.preview_rule(request.get("profile", {}), request.get("policy", {})))
                    return
                if self.path == "/api/library/save":
                    workspace._revision(request.get("revision"))
                    item, status = library.prepare(request.get("item", {}))
                    saved = workspace.save_library(item, status, request.get("revision"))
                    controllers.notify_reload(scope or spaces.state["active"])
                    self.json_response(200, saved)
                    return
                match = re.fullmatch(r"/api/library/([A-Za-z0-9_-]+)/(refresh|delete)", self.path)
                if match:
                    ident, action = match.groups()
                    if action == "delete":
                        self.json_response(200, workspace.delete_library(ident, request.get("revision")))
                    else:
                        library.refresh(ident)
                        self.json_response(200, workspace.snapshot())
                    return
                match = re.fullmatch(r"/api/groups/([A-Za-z0-9_-]+)/test", self.path)
                if match:
                    subscriptions.test_group(match.group(1))
                    self.json_response(200, workspace.snapshot())
                    return
                if self.path == "/api/sources":
                    ident = workspace.save_source(request.get("source", {}), revision=request.get("revision"))
                    self.json_response(200, {"id": ident, "workspace": workspace.snapshot()})
                    return
                if self.path == "/api/sources/reorder":
                    self.json_response(200, workspace.reorder_sources(request.get("ids"), request.get("revision")))
                    return
                if self.path == "/api/nodes/import":
                    self.json_response(200, workspace.import_nodes(request.get("text", ""), request.get("revision")))
                    return
                if self.path == "/api/nodes/test":
                    subscriptions.test_nodes(request.get("ids"))
                    self.json_response(200, workspace.snapshot())
                    return
                match = re.fullmatch(r"/api/sources/([A-Za-z0-9_-]+)/(save|refresh|test|delete)", self.path)
                if match:
                    ident, action = match.groups()
                    if action in {"save", "delete"} and subscriptions.busy(ident):
                        raise Conflict("该订阅正在刷新或测速，请任务结束后再修改或删除")
                    if action == "save":
                        workspace.save_source(request.get("source", {}), ident, request.get("revision"))
                    elif action == "delete":
                        workspace.delete_source(ident, request.get("revision"))
                    elif action == "refresh":
                        subscriptions.refresh(ident)
                    else:
                        subscriptions.test(ident)
                    self.json_response(200, workspace.snapshot())
                    return
                if self.path != "/api/export":
                    self.json_response(404, {"error": "接口不存在"})
                    return
                profile = spaces.workspace_snapshot(scope)["profile"] if request.get("use_workspace") else request.get("profile")
                bundle = compile_bundle(profile, request.get("proxy", "mihomo"), request.get("group_mode_overrides"), request.get("rule_output", "inline"), request.get("component", "both"))
                validate_bundle(bundle, root)
                output = io.BytesIO()
                with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                    for filename, config in bundle["files"].items():
                        archive.writestr(filename, serialize_config(config))
                    archive.writestr("manifest.json", json.dumps(bundle["manifest"], ensure_ascii=False, indent=2) + "\n")
                raw = output.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", 'attachment; filename="yomesh-' + (bundle["manifest"].get("dns_engine", "mosdns") if bundle["manifest"].get("component") == "dns" else bundle["manifest"]["proxy"]) + ('-' + bundle["manifest"].get("dns_engine", "mosdns") if bundle["manifest"].get("component", "both") == "both" else '') + '.zip"')
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except AuthUnavailable as error:
                self.json_response(503, {"error": str(error), "auth_unavailable": True})
            except HermesUnavailable as error:
                self.json_response(503, {"error": str(error)})
            except Conflict as error:
                self.json_response(409, {"error": str(error)})
            except (ConfigError, ValueError, TypeError, KeyError) as error:
                self.json_response(422, {"error": str(error)})
            except AttributeError:
                self.json_response(422, {"error": "配置结构无效，请检查字段类型"})
            except OSError:
                self.json_response(503, {"error": "无法读写工作区或运行内核，请检查数据目录权限与内核安装"})

    return Handler


def serve(root: Path, port=8767, host="127.0.0.1"):
    if not 1024 <= port <= 65535:
        raise ConfigError("UI 端口需要为 1024–65535")
    server = ThreadingHTTPServer((host, port), handler_for(root, port))
    print(f"YoMesh: http://{host}:{port} (Ctrl+C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.RequestHandlerClass.space_catalog.close()
        server.server_close()
