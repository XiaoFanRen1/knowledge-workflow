"""Export one Git commit, build its wheel, and assemble a hash-inventoried bundle."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


def sha(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def extract(archive, destination):
    with zipfile.ZipFile(archive) as zipped:
        for name in zipped.namelist():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or ":" in name or "\\" in name:
                raise ValueError("unsafe_archive_entry")
        zipped.extractall(destination)


parser = argparse.ArgumentParser()
parser.add_argument("--commit", required=True)
parser.add_argument("--wheelhouse", required=True, type=Path)
parser.add_argument("--destination", required=True, type=Path)
args = parser.parse_args()
repo = Path(__file__).resolve().parents[1]
destination = args.destination.resolve()
if destination.exists():
    raise FileExistsError("release destination already exists")
commit = subprocess.check_output(["git", "rev-parse", "--verify", args.commit + "^{commit}"], cwd=repo, text=True).strip()
with tempfile.TemporaryDirectory(prefix="kw-release-export-") as temporary:
    temporary = Path(temporary)
    archive, exported = temporary / "tree.zip", temporary / "source"
    subprocess.run(["git", "archive", "--format=zip", "--output", str(archive), commit], cwd=repo, check=True)
    extract(archive, exported)
    metadata = json.loads((exported / "plugins/knowledge-workflow/.codex-plugin/plugin.json").read_text(encoding="utf-8"))
    manifest = json.loads((exported / "requirements/wheels.json").read_text(encoding="utf-8"))
    destination.mkdir(parents=True)
    runtime = destination / "runtime"
    runtime.mkdir()
    subprocess.run([sys.executable, "-I", "-m", "pip", "wheel", "--isolated", "--no-deps", "--no-build-isolation",
                    "--disable-pip-version-check", "--wheel-dir", str(runtime), str(exported)], check=True)
    candidates = list(runtime.glob("knowledge_workflow-*.whl"))
    if len(candidates) != 1:
        raise ValueError("runtime wheel inventory mismatch")
    for name in ("install.py", "install.cmd", "README.md", "LICENSE"):
        shutil.copyfile(exported / name, destination / name)
    if (exported / "docs").is_dir():
        shutil.copytree(exported / "docs", destination / "docs")
    if (exported / "THIRD_PARTY_NOTICES.md").is_file():
        shutil.copyfile(exported / "THIRD_PARTY_NOTICES.md", destination / "THIRD_PARTY_NOTICES.md")
    if (exported / "licenses").is_dir():
        shutil.copytree(exported / "licenses", destination / "licenses")
    (destination / "requirements").mkdir()
    for name in ("windows-cp314.lock.txt", "wheels.json"):
        shutil.copyfile(exported / "requirements" / name, destination / "requirements" / name)
    (destination / "wheels").mkdir()
    for item in manifest["artifacts"]:
        source = args.wheelhouse / item["filename"]
        if source.stat().st_size != item["bytes"] or sha(source) != item["sha256"]:
            raise ValueError("wheel hash mismatch: " + item["name"])
        shutil.copyfile(source, destination / "wheels" / source.name)
    marketplace = destination / "marketplace"
    shutil.copytree(exported / ".agents", marketplace / ".agents")
    shutil.copytree(exported / "plugins", marketplace / "plugins")
    records = {p.relative_to(destination).as_posix(): {"sha256": sha(p), "bytes": p.stat().st_size}
               for p in sorted(destination.rglob("*")) if p.is_file()}
    model = json.loads((exported / "src/knowledge_workflow/assets/model.json").read_text(encoding="utf-8"))
    release = {"schema_version": 1, "version": metadata["version"], "commit": commit,
        "runtime_wheel": candidates[0].relative_to(destination).as_posix(), "model_revision": model["revision"],
        "files": records}
    (destination / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"commit": commit, "bundle": str(destination), "files": len(records),
                  "bytes": sum(item["bytes"] for item in records.values())}))
