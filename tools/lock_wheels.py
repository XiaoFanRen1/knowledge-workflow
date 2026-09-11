"""Freeze a reviewed wheelhouse without capturing the build machine environment."""
import argparse
import email
import hashlib
import json
import re
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("wheelhouse", type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
records = []
names = set()
for wheel in sorted(args.wheelhouse.glob("*.whl")):
    with zipfile.ZipFile(wheel) as archive:
        metadata_paths = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA") and n.count("/") == 1]
        if len(metadata_paths) != 1:
            raise ValueError("wheel metadata is ambiguous")
        metadata = email.message_from_bytes(archive.read(metadata_paths[0]))
        name = re.sub(r"[-_.]+", "-", metadata["Name"]).lower()
        if name in names:
            raise ValueError("multiple versions or artifacts require explicit resolution: " + name)
        names.add(name)
        licenses = [n for n in archive.namelist() if ".dist-info/" in n and
                    any(part in n.lower() for part in ("license", "copying", "notice")) and not n.endswith("/")]
        records.append({"name": name, "version": metadata["Version"], "filename": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(), "bytes": wheel.stat().st_size,
            "license_expression": metadata.get("License-Expression"),
            "license_metadata": (metadata.get("License") or "")[:300], "license_files": licenses})
if "pymupdf" in names or not {"jieba", "pypdf", "mcp", "torch"} <= names:
    raise ValueError("unexpected runtime dependency inventory")
args.output.mkdir(parents=True, exist_ok=True)
lines = ["# Windows x64 / CPython 3.14. Generated from the exact reviewed wheel artifacts.", "--only-binary=:all:"]
lines += [f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}" for r in sorted(records, key=lambda r: r["name"])]
(args.output / "windows-cp314.lock.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
(args.output / "wheels.json").write_text(json.dumps({"schema_version": 1, "platform": "win_amd64",
    "python": "cp314", "artifacts": records}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"wheels": len(records), "bytes": sum(r["bytes"] for r in records),
                  "without_license_file": [r["name"] for r in records if not r["license_files"]]}))
