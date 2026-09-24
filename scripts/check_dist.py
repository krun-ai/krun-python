"""Inspect built distributions before publishing: expected files present, nothing that must not ship.

python scripts/check_dist.py dist
"""

from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

FORBIDDEN = (".env", ".pem", ".key", "id_rsa", ".venv/", "__pycache__", ".pyc", "bench", ".github/")
WHEEL_REQUIRED = {"krun/__init__.py", "krun/_client.py", "krun/types.py", "krun/py.typed"}


def main(dist: str) -> int:
    root = Path(dist)
    wheels = list(root.glob("*.whl"))
    sdists = list(root.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        print(f"expected exactly one wheel and one sdist in {dist}, got {wheels + sdists}")
        return 1
    ok = True
    with zipfile.ZipFile(wheels[0]) as zf:
        wheel_files = zf.namelist()
    with tarfile.open(sdists[0]) as tf:
        sdist_files = [m.name for m in tf.getmembers() if m.isfile()]
    for name, files in (("wheel", wheel_files), ("sdist", sdist_files)):
        print(f"{name}: {len(files)} files")
        for f in files:
            print(f"  {f}")
            if any(bad in f for bad in FORBIDDEN):
                print(f"  ^^^ forbidden in {name}")
                ok = False
    missing = WHEEL_REQUIRED - set(wheel_files)
    if missing:
        print(f"wheel is missing {sorted(missing)}")
        ok = False
    if any(f.startswith("tests/") or "/tests/" in f for f in wheel_files):
        print("wheel must not contain tests")
        ok = False
    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist"))
