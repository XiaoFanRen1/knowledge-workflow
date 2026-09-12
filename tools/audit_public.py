"""Audit committed public bytes, including earlier reachable history."""
import argparse
import json
import re
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--commit", default="HEAD")
parser.add_argument("--history", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
allowed_roots = {"src", "tests", "tools", "docs", "plugins", "requirements", "licenses", "examples", ".agents", ".github"}
allowed_files = {".gitignore", ".gitattributes", "AGENTS.md", "README.md", "README.en.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "pyproject.toml", "install.py", "install.cmd", "recover_install.py"}
forbidden_suffixes = {".sqlite", ".db", ".zip", ".whl", ".safetensors", ".bin", ".log", ".pyc"}
patterns = [re.compile(rb"(?:ghp_|github_pat_|sk-proj-)[A-Za-z0-9_]{20,}"),
            re.compile(rb"[A-Za-z]:[/\\](?:Users[/\\][^/\\\s\"']+|WORK-[0-9]+|ObsidianVault)[/\\]", re.I)]
commits = (subprocess.check_output(["git", "rev-list", args.commit], cwd=root, text=True).splitlines()
           if args.history else [args.commit])
objects = {}
problems = []
for commit in commits:
    data = subprocess.check_output(["git", "ls-tree", "-r", "-z", commit], cwd=root)
    for item in data.split(b"\0"):
        if not item:
            continue
        meta, path = item.split(b"\t", 1)
        mode, kind, object_id = meta.decode().split()
        name = path.decode("utf-8")
        parts = Path(name).parts
        if mode == "120000" or kind != "blob":
            problems.append({"path": name, "reason": "linked_or_nonblob_source"})
        if name not in allowed_files and parts[0] not in allowed_roots:
            problems.append({"path": name, "reason": "outside_public_allowlist"})
        if Path(name).suffix.lower() in forbidden_suffixes:
            problems.append({"path": name, "reason": "runtime_or_private_artifact"})
        objects.setdefault(object_id, set()).add(name)
process = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
for object_id, names in objects.items():
    process.stdin.write((object_id + "\n").encode())
    process.stdin.flush()
    header = process.stdout.readline().decode().split()
    size = int(header[2])
    body = process.stdout.read(size)
    process.stdout.read(1)
    if size > 2 * 1024 * 1024:
        problems.append({"paths": sorted(names), "reason": "oversized_source_blob"})
    if any(pattern.search(body) for pattern in patterns):
        problems.append({"paths": sorted(names), "reason": "credential_or_machine_path_pattern"})
process.stdin.close()
process.wait(timeout=10)
print(json.dumps({"ok": not problems, "commits": len(commits), "unique_blobs": len(objects), "problems": problems}, indent=2))
raise SystemExit(1 if problems else 0)
