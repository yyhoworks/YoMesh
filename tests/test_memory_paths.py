"""Scheduling and native dependency checks avoid retaining full rule snapshots."""
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import Mock, patch

from test_native_configuration import fixture
from yomesh.model import ConfigError
from yomesh.native_configuration import resolve, validate_rule_yaml, _rule_yaml_cache, _rule_yaml_cache_lock
from yomesh.services import SubscriptionService
from yomesh.workspace import Workspace


class MemoryPathTests(unittest.TestCase):
    def test_schedule_projects_only_due_ids_without_copying_payloads(self):
        class LargePayload:
            def __deepcopy__(self, memo):
                raise AssertionError("Scheduler copied rule payload")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(directory)
            workspace.state["profile"]["sources"] = [
                {"id": "due"}, {"id": "disabled", "enabled": False},
                {"id": "manual", "auto_update": False}, {"id": "future"}]
            workspace.state["source_status"] = {ident: {"next_update": timestamp} for ident, timestamp in (("due", 90), ("disabled", 90), ("manual", 90), ("future", 101))}
            workspace.state["profile"]["library"] = [{"id": "rule", "entries": LargePayload(), "source": {"auto_update": True}}, {"id": "local", "source": {"auto_update": False}}]
            workspace.state["library_status"] = {ident: {"next_update": 90} for ident in ("rule", "local")}
            with patch.object(workspace, "snapshot", side_effect=AssertionError("Full snapshot requested")):
                self.assertEqual(workspace.due_updates(100), {"sources": ["due"], "rules": ["rule"]})
                service = SubscriptionService(directory, workspace, rule_library=Mock())
                with patch("yomesh.workspace.time.time", return_value=100), patch.object(service, "refresh") as refresh:
                    service._run_scheduled_updates()
                    refresh.assert_called_once_with("due")
                    service.rule_library.refresh.assert_called_once_with("rule")

    def test_failed_job_does_not_prevent_other_due_jobs(self):
        workspace = Mock()
        workspace.due_updates.return_value = {"sources": ["one", "two"], "rules": ["rule1", "rule2"]}
        library = Mock()
        library.refresh.side_effect = [ConfigError("busy"), None]
        service = SubscriptionService(".", workspace, rule_library=library)
        with patch.object(service, "refresh", side_effect=[ConfigError("busy"), None]) as refresh:
            service._run_scheduled_updates()
            self.assertEqual(refresh.call_count, 2)
            self.assertEqual(library.refresh.call_count, 2)

    def test_yaml_cache_keeps_digest_and_dependencies_only(self):
        body = "payload:\n- DOMAIN-SUFFIX,example.com\n- RULE-SET,one\n"
        self.assertEqual(validate_rule_yaml(body), ("one",))
        with _rule_yaml_cache_lock:
            self.assertTrue(all(isinstance(key, bytes) and len(key) == 32 for key in _rule_yaml_cache))
            self.assertNotIn(body, _rule_yaml_cache)
            self.assertLessEqual(len(_rule_yaml_cache), 128)
        with self.assertRaises(ConfigError):
            validate_rule_yaml(body + "- 123\n")

    def test_dependency_resolution_uses_cached_metadata_and_keeps_native_text(self):
        native = fixture()
        body = "payload:\n- RULE-SET,one\n- DOMAIN-SUFFIX,cached.example\n"
        native["mihomo"]["rule-providers"]["cached"] = {"type": "file", "path": "rules/cached.yaml", "behavior": "classical"}
        native["documents"]["rules/cached.yaml"] = body
        native["policies"][0]["text"] = "AND,((RULE-SET,cached),(NETWORK,udp)),直连组"
        before = deepcopy(native)
        validate_rule_yaml(body)
        with patch("yomesh.native_configuration.load_yaml", side_effect=AssertionError("Unchanged rule parsed again")):
            _, config, _ = resolve(native)
        self.assertEqual(set(config["rule-providers"]), {"one", "cached"})
        self.assertIn(native["policies"][0]["text"], config["rules"])
        self.assertEqual(native, before)
