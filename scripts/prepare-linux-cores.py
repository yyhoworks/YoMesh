"""Fetch hash-pinned Linux cores; unpack only named binaries and license files."""
from pathlib import Path
import gzip
import hashlib
import json
import shutil
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = json.loads((ROOT / "tools/cores.linux-amd64.lock.json").read_text(encoding="utf-8"))
    cache = ROOT / "work/linux-downloads"
    cache.mkdir(parents=True, exist_ok=True)
    for name, spec in lock["cores"].items():
        archive = cache / spec["cache_name"]
        if not archive.exists():
            with urllib.request.urlopen(spec["url"], timeout=90) as incoming, archive.open("wb") as output:
                shutil.copyfileobj(incoming, output)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != spec["sha256"]:
            raise SystemExit(f"{name}: SHA256 mismatch; extraction refused")
        target = ROOT / "tools/linux-amd64" / name
        target.mkdir(parents=True, exist_ok=True)
        if spec["archive"] == "gzip":
            with gzip.open(archive, "rb") as incoming, (target / name).open("wb") as output:
                shutil.copyfileobj(incoming, output)
        elif spec["archive"] == "tar.gz":
            with tarfile.open(archive, "r:gz") as package:
                for entry in package.getmembers():
                    basename = Path(entry.name).name
                    if entry.isfile() and (basename == name or basename.upper().startswith("LICENSE")):
                        with package.extractfile(entry) as incoming, (target / basename).open("wb") as output:
                            shutil.copyfileobj(incoming, output)
        else:
            with zipfile.ZipFile(archive) as package:
                for entry in package.infolist():
                    basename = Path(entry.filename).name
                    if not entry.is_dir() and (basename == name or basename.upper().startswith("LICENSE")):
                        with package.open(entry) as incoming, (target / basename).open("wb") as output:
                            shutil.copyfileobj(incoming, output)
        binary = target / name
        if not binary.is_file():
            raise SystemExit(f"{name}: binary absent from archive")
        binary.chmod(0o755)
        print(f"{name} {spec['version']}: official archive SHA256 verified")


if __name__ == "__main__":
    main()
