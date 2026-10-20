"""Configuration isolation, migration and reload coverage."""
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomesh.spaces import ConfigurationSpaces
from yomesh.workspace import Workspace, Conflict
from yomesh.model import ConfigError
from yomesh.compiler import compile_bundle


class ConfigurationSpaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = json.loads((ROOT / "examples/profile.json").read_text(encoding="utf-8"))
        workspace = Workspace(self.root / "data")
        workspace.state["profile"] = deepcopy(self.profile)
        workspace.state["migrated"] = True
        workspace._write()
        self.before = workspace.file.read_bytes()
        self.spaces = ConfigurationSpaces(self.root)

    def tearDown(self):
        self.spaces.close()
        self.temporary.cleanup()

    def create(self, name="手机", copy_from="default", proxy="sing-box"):
        return self.spaces.save({"name": name, "description": "独立客户端", "proxy": proxy, "rule_output": "local"}, self.spaces.snapshot()["revision"], copy_from=copy_from)

    def test_legacy_workspace_migrates_without_rewriting_data(self):
        self.assertEqual(self.spaces.snapshot()["active"], "default")
        self.assertEqual(self.spaces.runtime().workspace.snapshot()["profile"], self.profile)
        self.assertEqual((self.root / "data/workspace.json").read_bytes(), self.before)

    def test_copy_keeps_profile_and_edits_are_independent_after_reload(self):
        result = self.create()
        ident = result["id"]
        state = self.spaces.workspace_snapshot(ident)
        self.assertEqual(state["profile"]["library"], self.profile["library"])
        self.assertEqual(state["profile"]["sources"], self.profile.get("sources", []))
        state["profile"]["settings"]["dns_port"] = 1153
        state["profile"]["base_configs"] = {"sing-box": '{"log":{"level":"warn"}}'}
        self.spaces.save_profile(state["profile"], state["revision"], ident)
        self.assertEqual(self.spaces.runtime("default").workspace.snapshot()["profile"], self.profile)
        self.spaces.select(ident, self.spaces.snapshot()["revision"])
        self.spaces.close()
        self.spaces = ConfigurationSpaces(self.root)
        self.assertEqual(self.spaces.snapshot()["active"], ident)
        self.assertEqual(self.spaces.workspace_snapshot()["profile"]["settings"]["dns_port"], 1153)
        self.assertEqual(self.spaces.snapshot()["spaces"][-1]["proxy"], "sing-box")

    def test_space_revision_names_and_missing_ids_are_protected(self):
        before = self.spaces.snapshot()
        self.create()
        with self.assertRaises(Conflict):
            self.spaces.select("default", before["revision"])
        with self.assertRaises(ConfigError):
            self.create()
        for ident in ("../default", "missing", ""):
            with self.assertRaises(ConfigError):
                self.spaces.runtime(ident)

    def test_blank_profile_and_delete_protect_default_and_active(self):
        result = self.create(copy_from=None)
        ident = result["id"]
        self.assertEqual(self.spaces.workspace_snapshot(ident)["profile"]["composition"]["groups"], [])
        self.spaces.select(ident, result["revision"])
        for protected in ("default", ident):
            with self.assertRaises(ConfigError):
                self.spaces.delete(protected, self.spaces.snapshot()["revision"])
        self.spaces.select("default", self.spaces.snapshot()["revision"])
        self.spaces.delete(ident, self.spaces.snapshot()["revision"])
        with self.assertRaises(ConfigError):
            self.spaces.runtime(ident)
        self.assertEqual(self.spaces.state["archived"][-1]["id"], ident)
        self.assertTrue((self.root / "data/workspace.json").exists())

    def test_background_status_and_generated_configs_stay_in_own_space(self):
        ident = self.create()["id"]
        phone = self.spaces.runtime(ident).workspace
        state = self.spaces.workspace_snapshot(ident)
        state["profile"]["base_configs"] = {"mihomo": "mixed-port: 18891\n"}
        self.spaces.save_profile(state["profile"], state["revision"], ident)
        phone.state["node_status"]["same-id"] = {"state": "failed"}
        phone._write()
        default = self.spaces.runtime("default").workspace.snapshot()
        self.assertIn("same-id", default["node_status"])
        first = compile_bundle(default["profile"], "mihomo")
        second = compile_bundle(self.spaces.workspace_snapshot(ident)["profile"], "mihomo")
        self.assertNotEqual(first["files"]["mihomo.yaml"]["mixed-port"], second["files"]["mihomo.yaml"]["mixed-port"])
        for key in ("proxies", "proxy-groups", "rules"):
            self.assertEqual(first["files"]["mihomo.yaml"][key], second["files"]["mihomo.yaml"][key])

    def test_definitions_shared_but_rule_selections_are_per_space(self):
        ident = self.create()["id"]
        state = self.spaces.workspace_snapshot(ident)
        state["profile"]["groups"][0]["name"] = "共享的新组名"
        self.spaces.save_profile(state["profile"], state["revision"], ident)
        self.assertEqual(self.spaces.workspace_snapshot("default")["profile"]["groups"][0]["name"], "共享的新组名")
        state = self.spaces.workspace_snapshot(ident)
        self.spaces.save_selection({"rules": []}, state["revision"], ident)
        self.assertEqual(self.spaces.workspace_snapshot(ident)["profile"]["composition"]["rules"], [])
        self.assertNotIn("composition", self.spaces.workspace_snapshot("default")["profile"])
        self.assertIs(self.spaces.runtime(ident), self.spaces.runtime("default"))

    def test_invalid_selection_save_does_not_create_a_space(self):
        before = self.spaces.snapshot()
        with self.assertRaises(ConfigError):
            self.spaces.save({"name": "错误", "composition": {"rules": ["missing"]}}, before["revision"], copy_from="default")
        self.assertEqual(before, self.spaces.snapshot())

    def test_draft_preview_is_read_only_and_resource_revision_protects_save(self):
        before = self.spaces.workspace_snapshot("default")
        catalog = self.spaces.snapshot()
        files = {p.name: p.read_bytes() for p in (self.root / "data").glob("*.json")}
        preview = self.spaces.preview_selection({"rules": []}, before["revision"], "default")
        self.assertTrue(preview["valid"], preview["error"])
        self.assertGreater(len(preview["excluded_policies"]), 0)
        self.assertEqual(files, {p.name: p.read_bytes() for p in (self.root / "data").glob("*.json")})
        self.spaces.runtime("default").workspace._write()
        with self.assertRaises(Conflict):
            self.spaces.save({"name": "过期草稿"}, catalog["revision"], copy_from="default", workspace_revision=before["revision"])
        self.assertEqual(len(self.spaces.snapshot()["spaces"]), 1)

    def test_card_generation_and_preview_agree_after_rule_selection_reload(self):
        ident = self.create()["id"]
        state = self.spaces.workspace_snapshot(ident)
        self.spaces.save_selection({"rules": []}, state["revision"], ident)
        snapshot = self.spaces.workspace_snapshot(ident)
        card = next(s for s in self.spaces.snapshot()["spaces"] if s["id"] == ident)
        self.assertEqual(card["generation"]["counts"], snapshot["generation"]["counts"])
        self.assertEqual(card["generation"]["excluded_policies"], snapshot["generation"]["excluded_policies"])
