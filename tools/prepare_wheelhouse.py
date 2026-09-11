"""Fetch locked public artifacts; rebuild the sole source-only package in CI."""
import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_wheel import normalize


def fetch(url, destination, expected):
    with urllib.request.urlopen(url, timeout=60) as response, destination.open("wb") as stream:
        while block := response.read(1024 * 1024):
            stream.write(block)
    if hashlib.sha256(destination.read_bytes()).hexdigest() != expected:
        raise ValueError("artifact download hash mismatch")


parser = argparse.ArgumentParser()
parser.add_argument("--wheelhouse", required=True, type=Path)
parser.add_argument("--only", help="Build or verify one named dependency during release diagnostics")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
records = json.loads((root / "requirements/wheels.json").read_text(encoding="utf-8"))["artifacts"]
args.wheelhouse.mkdir(parents=True, exist_ok=True)
for item in records:
    if args.only and item["name"] != args.only:
        continue
    target = args.wheelhouse / item["filename"]
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
        continue
    print("dependency: " + item["name"], flush=True)
    with urllib.request.urlopen(f"https://pypi.org/pypi/{item['name']}/{item['version']}/json", timeout=30) as response:
        metadata = json.load(response)
    if item["name"] == "jieba":
        source_hash = "055ca12f62674fafed09427f176506079bc135638a14e23e25be909131928db2"
        artifact = next(value for value in metadata["urls"] if value["digests"]["sha256"] == source_hash)
        with tempfile.TemporaryDirectory(prefix="kw-source-wheel-") as temporary:
            source = Path(temporary) / artifact["filename"]
            fetch(artifact["url"], source, source_hash)
            subprocess.run([sys.executable, "-I", "-m", "pip", "wheel", "--isolated", "--no-index", "--no-deps", "--no-build-isolation",
                            "--wheel-dir", str(args.wheelhouse), str(source)], check=True)
        actual = normalize(target)
        if actual != item["sha256"]:
            raise ValueError("rebuilt wheel differs from the release artifact")
    else:
        artifact = next(value for value in metadata["urls"] if value["filename"] == item["filename"] and value["digests"]["sha256"] == item["sha256"])
        fetch(artifact["url"], target, item["sha256"])
