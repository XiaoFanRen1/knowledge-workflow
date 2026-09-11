"""Validate exact artifacts. Locally built wheels must match the release manifest."""
import argparse
import hashlib
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--wheelhouse", required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
items = json.loads((root / "requirements/wheels.json").read_text(encoding="utf-8"))["artifacts"]
problems = []
for item in items:
    path = args.wheelhouse / item["filename"]
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        problems.append(item["filename"])
print(json.dumps({"ok": not problems, "artifacts": len(items), "mismatches": problems}, indent=2))
raise SystemExit(1 if problems else 0)
