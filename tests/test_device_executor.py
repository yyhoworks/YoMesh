"""Linux-only isolated tests for the restricted on-demand executor."""
import base64
import hashlib
import importlib.util
import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "device_executor.py"


@unittest.skipUnless(sys.platform.startswith("linux"), "设备执行器仅运行于 Linux")
class DeviceExecutorTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("yomesh_test_device_executor", RUNNER)
        self.runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.runner)
        self.temp = tempfile.TemporaryDirectory(prefix="yomesh-executor-test-")
        self.root = Path(self.temp.name)
        mihomo = self.root / "mihomo"
        localdns = self.root / "localdns"
        (localdns / "config").mkdir(parents=True)
        (localdns / "rules").mkdir()
        mihomo.mkdir()
        (mihomo / "config.yaml").write_bytes(b"old mihomo\n")
        (mihomo / "old-provider.yaml").write_bytes(b"old provider\n")
        (localdns / "config" / "localdns.json").write_bytes(b"old dns\n")
        (localdns / "rules" / "old.txt").write_bytes(b"old rule\n")
        self.policy = {"format": "yomesh-executor-v1", "manager": "openrc",
                       "mihomo_home": str(mihomo), "mihomo_config": str(mihomo / "config.yaml"),
                       "mihomo_binary": str(self.root / "mihomo-bin"),
                       "localdns_root": str(localdns), "localdns_binary": str(self.root / "dns-bin"),
                       "state_dir": str(self.root / "state"),
                       "services": {"mihomo": "fixture-mihomo", "localdns": "fixture-localdns"}}
        self.baseline = self.runner._manifest(self.runner._capture(self.policy, ["old-provider.yaml"]))
        self.patches = [patch.object(self.runner, "_validate", return_value={"mihomo": "passed", "localdns": "passed"}),
                        patch.object(self.runner, "_service"), patch.object(self.runner, "_healthy")]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def payload(self, files, ident="release_" + "a" * 32):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, body in files.items():
                archive.writestr(name, body)
        blob = output.getvalue()
        return {"release_id": ident, "baseline": self.baseline, "refs": ["old-provider.yaml"],
                "bundle": base64.b64encode(blob).decode(), "bundle_sha256": hashlib.sha256(blob).hexdigest(),
                "versions": {"mihomo": "fixture", "localdns": "fixture"}}

    def test_new_changed_and_removed_files_apply_then_rollback(self):
        files = {"mihomo.yaml": b"new mihomo\n", "localdns/config/localdns.json": b"new dns\n",
                 "new-provider.yaml": b"new provider\n"}
        payload = self.payload(files)
        self.assertEqual(self.runner._prepare(self.policy, payload)["status"], "prepared")
        state = self.runner._state(self.policy, payload["release_id"])
        self.assertIn("new-provider.yaml", state["writes"])
        self.assertIn("old-provider.yaml", state["deletes"])
        self.runner._write_state(self.policy, payload["release_id"], {**state, "status": "running"})
        self.runner._apply(self.policy, payload["release_id"])
        self.assertEqual(self.runner._state(self.policy, payload["release_id"])["status"], "succeeded")
        self.assertEqual((self.root / "mihomo" / "new-provider.yaml").read_bytes(), b"new provider\n")
        self.assertFalse((self.root / "mihomo" / "old-provider.yaml").exists())
        self.assertFalse((self.root / "localdns" / "rules" / "old.txt").exists())
        self.assertEqual(self.runner._dispatch(self.policy, "rollback", {"release_id": payload["release_id"]})["status"], "rolled_back")
        self.assertEqual((self.root / "mihomo" / "config.yaml").read_bytes(), b"old mihomo\n")
        self.assertTrue((self.root / "mihomo" / "old-provider.yaml").exists())
        self.assertTrue((self.root / "localdns" / "rules" / "old.txt").exists())
        self.assertFalse((self.root / "mihomo" / "new-provider.yaml").exists())

    def test_untracked_existing_target_and_changed_baseline_are_rejected(self):
        files = {"mihomo.yaml": b"new mihomo\n", "localdns/config/localdns.json": b"old dns\n",
                 "new-provider.yaml": b"new provider\n"}
        (self.root / "mihomo" / "new-provider.yaml").write_bytes(b"untracked\n")
        with self.assertRaisesRegex(self.runner.Rejected, "未纳入基准"):
            self.runner._prepare(self.policy, self.payload(files))
        self.assertFalse((self.root / "state" / ("release_" + "a" * 32)).exists())
        (self.root / "mihomo" / "new-provider.yaml").unlink()
        payload = self.payload(files, "release_" + "b" * 32)
        self.runner._prepare(self.policy, payload)
        state = self.runner._state(self.policy, payload["release_id"])
        (self.root / "mihomo" / "config.yaml").write_bytes(b"external edit\n")
        self.runner._write_state(self.policy, payload["release_id"], {**state, "status": "running"})
        self.runner._apply(self.policy, payload["release_id"])
        self.assertEqual(self.runner._state(self.policy, payload["release_id"])["status"], "conflict")
        self.assertEqual((self.root / "mihomo" / "config.yaml").read_bytes(), b"external edit\n")
