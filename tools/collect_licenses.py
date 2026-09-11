"""Preserve dependency license text from pinned wheels and two pinned upstream tags."""
import argparse
import hashlib
import json
import urllib.request
import zipfile
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--wheelhouse", required=True, type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
manifest = json.loads((root / "requirements/wheels.json").read_text(encoding="utf-8"))
destination = root / "licenses"
destination.mkdir(exist_ok=True)
records = []


def save(package, name, raw, origin):
    text = raw.decode("utf-8-sig")
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"
    path = destination / package / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(normalized, encoding="utf-8", newline="\n")
    records.append({"package": package, "path": path.relative_to(root).as_posix(), "source": origin,
                    "source_sha256": hashlib.sha256(raw).hexdigest(),
                    "stored_sha256": hashlib.sha256(normalized.encode()).hexdigest()})


for item in manifest["artifacts"]:
    wheel = args.wheelhouse / item["filename"]
    if hashlib.sha256(wheel.read_bytes()).hexdigest() != item["sha256"]:
        raise ValueError("wheel integrity mismatch")
    with zipfile.ZipFile(wheel) as archive:
        for i, name in enumerate(item["license_files"]):
            save(item["name"], str(i + 1) + "-" + Path(name).name + ".txt", archive.read(name), item["filename"] + ":" + name)
for package, url in {
    "jieba": "https://raw.githubusercontent.com/fxsjy/jieba/v0.42.1/LICENSE",
    "tokenizers": "https://raw.githubusercontent.com/huggingface/tokenizers/v0.22.2/LICENSE",
    "embedding-model": "https://www.apache.org/licenses/LICENSE-2.0.txt",
}.items():
    with urllib.request.urlopen(url, timeout=30) as response:
        save(package, "LICENSE.txt", response.read(), url)
covered = {item["package"] for item in records}
missing = {item["name"] for item in manifest["artifacts"]} - covered
if missing:
    raise ValueError("license coverage missing: " + ", ".join(sorted(missing)))
(destination / "sources.json").write_text(json.dumps({"formatting": "Line endings and trailing whitespace normalized; wording retained.",
    "records": records}, indent=2) + "\n", encoding="utf-8", newline="\n")
print(json.dumps({"dependencies_covered": len(covered) - 1, "model_license": True, "license_files": len(records)}))
