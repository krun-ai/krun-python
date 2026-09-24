"""Compare the live OpenAPI document with the snapshot this SDK was written against.

    python scripts/check_openapi.py            # exit 0: identical, 1: drift (prints what changed), 2: fetch failed
    python scripts/check_openapi.py --update   # overwrite openapi/openapi.json and print the new hash/version

After --update: set OPENAPI_SHA256 / OPENAPI_VERSION in src/krun/_constants.py and run the tests; the contract tests
(tests/test_contract.py) point at every model that has to follow the change.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "openapi" / "openapi.json"


def canonical_sha256(doc: Any) -> str:
    return hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def diff(old: Any, new: Any, path: str = "$") -> list[str]:
    if isinstance(old, dict) and isinstance(new, dict):
        out = [f"+ {path}.{k}" for k in new.keys() - old.keys()]
        out += [f"- {path}.{k}" for k in old.keys() - new.keys()]
        for k in old.keys() & new.keys():
            out += diff(old[k], new[k], f"{path}.{k}")
        return sorted(out)
    return [] if old == new else [f"~ {path}: {json.dumps(old)[:80]} -> {json.dumps(new)[:80]}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="https://api.krun.ai/openapi.json")
    parser.add_argument("--update", action="store_true", help="overwrite the snapshot with the live document")
    args = parser.parse_args()
    try:
        req = urllib.request.Request(args.url, headers={"User-Agent": "krun-python-openapi-check"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            live = json.load(resp)
    except Exception as exc:
        print(f"could not fetch {args.url}: {exc}", file=sys.stderr)
        return 2
    snapshot = json.loads(SNAPSHOT.read_text())
    if args.update:
        SNAPSHOT.write_text(json.dumps(live, indent=2, ensure_ascii=False) + "\n")
        print(f"snapshot updated: version={live['info']['version']} sha256={canonical_sha256(live)}")
        return 0
    if canonical_sha256(live) == canonical_sha256(snapshot):
        print(f"OK: live OpenAPI {live['info']['version']} matches the snapshot ({canonical_sha256(live)[:12]})")
        return 0
    print(f"DRIFT: live OpenAPI ({live['info']['version']}) differs from openapi/openapi.json:")
    for line in diff(snapshot, live):
        print("  " + line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
