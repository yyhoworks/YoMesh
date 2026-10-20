"""Selective rule dependencies, atomic ordering and DNS settings persistence."""
import json
import sys
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.compiler import compile_bundle
from yomesh.model import ConfigError, Profile
from yomesh.native import validate_bundle
from yomesh.workspace import Conflict, Workspace
from yomesh.services import SubscriptionService


def logical(ident, libraries, enabled=True):
    return {"id": ident, "name": ident, "type": "LOGICAL", "condition_groups": [{"operator": "or", "conditions": [{"type": "RULE-SET", "value": i} for i in libraries]}], "group_operator": "or", "target": "DIRECT", "enabled": enabled}


def profile():
    libraries = [{"id": i, "name": i, "kind": "domain", "entries": [i + ".example"]} for i in ("local", "remote", "home", "unused", "disabled")]
    libraries[1]["source"] = {"type": "url", "url": "https://example.com/rules", "auto_update": False}
    active = {"id": "active", "name": "active", "policies": [logical("combined", ["local", "remote"]), logical("off", ["disabled"], False)], "fallback": "DIRECT"}
    home = {"id": "home_plan", "name": "home", "policies": [logical("home_policy", ["home"])], "fallback": "DIRECT"}
    unused = {"id": "unused_plan", "name": "unused", "policies": [logical("unused_policy", ["unused"])], "fallback": "DIRECT"}
    return {"schema_version": 1, "sources": [], "nodes": [], "groups": [], "library": libraries, "policies": deepcopy(active["policies"]), "fallback": "DIRECT", "active_plan": "active", "routing_plans": [active, home, unused], "inbounds": [{"id": "home_in", "name": "home", "protocol": "mixed", "listen": "127.0.0.1", "port": 18901, "enabled": True, "plan": "home_plan"}], "settings": {}}


class OperationsTests(unittest.TestCase):
    def test_only_selected_runtime_dependencies_are_compiled_and_load(self):
        value = profile()
        for target in ("mihomo", "sing-box"):
            bundle = compile_bundle(value, target)
            native = bundle["files"][target + (".yaml" if target == "mihomo" else ".json")]
            text = json.dumps(native)
            self.assertIn("home.example", text)
            self.assertNotIn("unused.example", text)
            self.assertNotIn("disabled.example", text)
            dns = json.dumps(bundle["files"]["mosdns.yaml"])
            self.assertIn("local.example", dns)
            self.assertIn("remote.example", dns)
            self.assertNotIn("home.example", dns)
            self.assertEqual(bundle["manifest"]["routing_usage"]["included"], ["home", "local", "remote"])
            self.assertEqual(validate_bundle(bundle, ROOT)["status"], "passed")
        self.assertEqual(set(compile_bundle(value)["files"]["mihomo.yaml"]["rule-providers"]), {"library_local", "library_remote", "library_home"})
        value["inbounds"][0]["enabled"] = False
        self.assertNotIn("library_home", compile_bundle(value)["files"]["mihomo.yaml"]["rule-providers"])

    def test_usage_distinguishes_current_disabled_and_unbound_references(self):
        usage = Profile.routing_usage(profile())
        active = next(p for p in usage["plans"] if p["current"])
        self.assertEqual(active["policies"][0]["sources"], {"local": 1, "subscription": 1})
        self.assertIn("或", active["policies"][0]["summary"])
        self.assertFalse(usage["library"]["disabled"]["runtime"])
        self.assertFalse(usage["library"]["unused"]["runtime"])
        self.assertEqual(usage["library"]["unused"]["references"][0]["plan"], "unused_plan")

    def test_reorder_is_atomic_preserves_other_plans_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            before = workspace.save_profile(profile(), 0)
            after = workspace.reorder_policies(["off", "combined"], "active", before["revision"])
            self.assertEqual([p["id"] for p in after["profile"]["policies"]], ["off", "combined"])
            self.assertEqual(after["profile"]["routing_plans"][0]["policies"], after["profile"]["policies"])
            self.assertEqual(after["profile"]["routing_plans"][1:], before["profile"]["routing_plans"][1:])
            self.assertEqual(Workspace(temporary).snapshot(), after)
            for ids, plan, revision, error in ((["combined", "combined"], "active", after["revision"], ConfigError), (["combined"], "active", after["revision"], ConfigError), (["off", "combined"], "home_plan", after["revision"], Conflict), (["off", "combined"], "active", before["revision"], Conflict)):
                with self.assertRaises(error):
                    workspace.reorder_policies(ids, plan, revision)
                self.assertEqual(workspace.snapshot(), after)

    def test_dns_settings_validate_atomically_without_changing_policies(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            before = workspace.save_profile(profile(), 0)
            settings = {**before["usage"]["settings"], "dns_default": "local", "local_upstream": "119.29.29.29", "remote_upstream": "tls://1.1.1.1", "dns_port": 1153}
            after = workspace.save_settings(settings, before["revision"])
            self.assertEqual(after["profile"]["policies"], before["profile"]["policies"])
            self.assertEqual(after["profile"]["routing_plans"], before["profile"]["routing_plans"])
            self.assertEqual(Workspace(temporary).snapshot()["profile"]["settings"], settings)
            preview = workspace.preview_dns(settings)
            self.assertEqual(next(p for p in preview["config"]["plugins"] if p["tag"] == "udp_listener")["args"]["listen"], "127.0.0.1:1153")
            for invalid in ({**settings, "mixed_port": 1153}, {**settings, "bootstrap": "resolver.example"}, {**settings, "unknown": True}, []):
                with self.assertRaises(ConfigError):
                    workspace.save_settings(invalid, after["revision"])
                self.assertEqual(workspace.snapshot(), after)

    def test_dns_preview_accepts_empty_group_drafts_without_saving(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            before = workspace.snapshot()
            self.assertTrue(workspace.preview_dns(before["usage"]["settings"])["config"]["plugins"])
            self.assertEqual(workspace.snapshot(), before)

    def test_selected_node_probe_rejects_invalid_selection_and_limits_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Workspace(temporary)
            value = profile()
            value["nodes"] = [{"id": i, "name": i, "source": "manual", "protocol": "shadowsocks", "server": "127.0.0.1", "port": 22001, "method": "aes-128-gcm", "password": "fixture"} for i in ("selected", "unselected")]
            workspace.save_profile(value, 0)
            service = SubscriptionService(ROOT, workspace)
            seen, done = [], threading.Event()
            def measure(options, nodes, group_id):
                seen.extend(n["id"] for n in nodes)
                done.set()
                return len(nodes)
            service._measure = measure
            for ids in ([], ["selected", "selected"], ["missing"]):
                with self.assertRaises(ConfigError):
                    service.test_nodes(ids)
            service.test_nodes(["selected"])
            self.assertTrue(done.wait(3))
            self.assertEqual(seen, ["selected"])
            deadline = time.monotonic() + 3
            while service.running and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(service.running)


if __name__ == "__main__":
    unittest.main()
