"""Run without installing dependencies into the user's global Python."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yomesh.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
