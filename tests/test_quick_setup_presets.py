"""Preset region/rule catalog shape checks; no network access."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yomesh.quick_setup import PRESET_REGIONS, PRESET_RULES


class PresetCatalogTests(unittest.TestCase):
    def test_region_and_rule_keys_are_unique_and_stable(self):
        self.assertEqual(len({r["key"] for r in PRESET_REGIONS}), len(PRESET_REGIONS))
        self.assertEqual(len({r["id"] for r in PRESET_REGIONS}), len(PRESET_REGIONS))
        self.assertEqual(len({p["key"] for p in PRESET_RULES}), len(PRESET_RULES))
        self.assertEqual(len({p["id"] for p in PRESET_RULES}), len(PRESET_RULES))

    def test_rule_targets_reference_known_regions_or_builtins(self):
        allowed = {r["key"] for r in PRESET_REGIONS} | {"DIRECT", "REJECT", "proxy"}
        regions = {r["key"] for r in PRESET_REGIONS} | {"direct", "reject", "proxy"}
        for preset in PRESET_RULES:
            with self.subTest(preset=preset["key"]):
                self.assertIn(preset["default_target"], allowed)
                self.assertEqual(preset["default_region"], preset["default_target"].lower())
                self.assertIn(preset["default_region"], regions)
                self.assertTrue(preset["pill"].strip())
                self.assertTrue(preset["name"].strip())
                self.assertTrue(preset["entries"])
                self.assertTrue(all(isinstance(e, str) and e.strip() for e in preset["entries"]))
                for field in ("url", "domain_url", "ip_url"):
                    value = preset.get(field, "")
                    if value:
                        self.assertTrue(value.startswith("https://"), field)

    def test_common_template_rule_packs_are_covered(self):
        keys = {p["key"] for p in PRESET_RULES}
        expected = {"ai", "youtube", "netflix", "telegram", "github", "apple", "cn", "adblock", "steam", "epicgames", "spotify", "docker", "microsoft", "themoviedb", "twitter"}
        self.assertEqual(keys, expected)


if __name__ == "__main__":
    unittest.main()
