"""Linux-only isolated installation tests; never touch real SSH files."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "device_installer.py"


@unittest.skipUnless(sys.platform.startswith("linux"), "目标安装器仅运行于 Linux")
class DeviceInstallerTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("yomesh_test_device_installer", INSTALLER)
        self.installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.installer)
        self.temp = tempfile.TemporaryDirectory(prefix="yomesh-installer-test-")
        self.root = Path(self.temp.name)
        home = self.root / "mihomo"
        dns = self.root / "localdns"
        home.mkdir()
        dns.mkdir()
        (home / "config.yaml").write_text("rules: []\n")
        (self.root / "mihomo-bin").write_bytes(b"binary")
        (dns / "localdns").write_bytes(b"binary")
        runner = self.root / "runner-source.py"
        runner.write_text("print('fixture')\n")
        self.installer.RUNNER = runner
        self.installer.DESTINATION = self.root / "installed" / "runner.py"
        self.installer.POLICY = self.root / "policy" / "target.json"
        self.installer.AUTHORIZED = self.root / "ssh" / "authorized_keys"
        self.installer.AUTHORIZED.parent.mkdir()
        self.installer.AUTHORIZED.write_text("ssh-rsa AAAA existing-key\n")
        settings = {"format": "yomesh-executor-v1", "manager": "openrc",
                    "mihomo_home": str(home), "mihomo_config": str(home / "config.yaml"),
                    "mihomo_binary": str(self.root / "mihomo-bin"), "localdns_root": str(dns),
                    "localdns_binary": str(dns / "localdns"), "state_dir": str(self.root / "state"),
                    "services": {"mihomo": "fixture-mihomo", "localdns": "fixture-dns"},
                    "validation_env": {"LD_PRELOAD": str(dns / "shim.so")}}
        self.settings = self.root / "settings.json"
        self.settings.write_text(json.dumps(settings))
        self.public = self.root / "device.pub"
        self.public.write_text("ssh-rsa AAAA yomesh-device_" + "a" * 32 + "\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_check_then_install_preserves_existing_authorized_key(self):
        self.assertTrue(self.installer.install(self.settings, self.public, dry_run=True)["ready"])
        self.assertFalse(self.installer.DESTINATION.exists())
        first = self.installer.install(self.settings, self.public)
        self.assertTrue(first["installed"])
        self.assertEqual(self.installer.DESTINATION.read_text(), "print('fixture')\n")
        lines = self.installer.AUTHORIZED.read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], "ssh-rsa AAAA existing-key")
        self.assertIn('command="', lines[1])
        self.assertIn("restrict ssh-rsa AAAA yomesh-device_", lines[1])
        second = self.installer.install(self.settings, self.public)
        self.assertNotEqual(first["backup"], second["backup"])
        self.assertEqual(len(self.installer.AUTHORIZED.read_text().splitlines()), 2)

    def test_broad_home_is_rejected_before_any_write(self):
        value = json.loads(self.settings.read_text())
        value["mihomo_home"] = "/"
        self.settings.write_text(json.dumps(value))
        with self.assertRaisesRegex(RuntimeError, "范围过宽"):
            self.installer.install(self.settings, self.public)
        self.assertFalse(self.installer.DESTINATION.exists())
