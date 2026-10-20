"""Keep each installation path on the same release and runtime dependencies."""
from __future__ import annotations

import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from yomesh import __version__


class ReleaseMetadataTests(unittest.TestCase):
    def test_package_compose_and_runtime_requirements_match(self):
        package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        requirements = {
            line.strip() for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        self.assertEqual(package["version"], __version__)
        self.assertEqual(set(package["dependencies"]), requirements)
        self.assertIn(f"image: yomesh:{__version__}", (ROOT / "compose.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
