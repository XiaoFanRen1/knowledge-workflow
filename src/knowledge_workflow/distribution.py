"""Verified distribution files and model acquisition without runtime downloads."""
import json
import os
import shutil
import sys
from pathlib import Path

from .models import local_snapshot
from .util import contained, digest, read_json, reject_links


def sha256_file(path):
    import hashlib
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def model_manifest():
    from importlib.resources import files
    return json.loads(files("knowledge_workflow").joinpath("assets/model.json").read_text(encoding="utf-8"))


def verify_model(directory, *, strict_inventory=True):
    directory = reject_links(directory).resolve()
    manifest = model_manifest()
    for item in manifest["files"]:
        path = contained(directory, item["path"])
        if not path.is_file() or path.stat().st_size != item["bytes"] or sha256_file(path) != item["sha256"]:
            raise ValueError("model_hash_mismatch: " + item["path"])
    allowed = {item["path"] for item in manifest["files"]}
    if strict_inventory and any(p.relative_to(directory).as_posix() not in allowed for p in directory.rglob("*") if p.is_file()):
        raise ValueError("unreviewed_model_file")
    return {"ok": True, "model": manifest["model"], "revision": manifest["revision"], "files": len(allowed)}


def acquire_model(destination, *, existing=None, offline=False):
    destination = reject_links(Path(destination)).resolve()
    manifest = model_manifest()
    source = local_snapshot(Path(existing)) if existing is not None else None
    destination.mkdir(parents=True, exist_ok=True)
    for item in manifest["files"]:
        target = contained(destination, item["path"])
        if target.exists():
            if target.stat().st_size == item["bytes"] and sha256_file(target) == item["sha256"]:
                continue
            raise ValueError("existing_model_file_modified: " + item["path"])
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".download")
        print("model: " + item["path"], file=sys.stderr, flush=True)
        try:
            if source is not None:
                original = contained(source, item["path"])
                if sha256_file(original) != item["sha256"]:
                    raise ValueError("offline_model_source_hash_mismatch")
                shutil.copyfile(original, temporary)
            elif offline:
                raise FileNotFoundError("offline_model_file_missing: " + item["path"])
            else:
                from .downloads import download_file
                url = f"https://huggingface.co/{manifest['model']}/resolve/{manifest['revision']}/{item['path']}"
                download_file(url, temporary, item["sha256"], expected_bytes=item["bytes"], label=item["path"])
            if temporary.stat().st_size != item["bytes"] or sha256_file(temporary) != item["sha256"]:
                raise ValueError("model_download_hash_mismatch")
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return verify_model(destination)


def verify_bundle(root):
    root = reject_links(Path(root)).resolve()
    manifest = read_json(root / "release.json")
    if manifest.get("schema_version") != 1 or not manifest.get("commit"):
        raise ValueError("unsupported_release_manifest")
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and p.relative_to(root).as_posix() != "release.json"}
    if actual != set(manifest["files"]):
        raise ValueError("release_inventory_mismatch")
    for relative, expected in manifest["files"].items():
        target = contained(root, relative)
        if target.stat().st_size != expected["bytes"] or sha256_file(target) != expected["sha256"]:
            raise ValueError("release_hash_mismatch: " + relative)
    if not manifest["runtime_wheel"].startswith("runtime/"):
        raise ValueError("invalid_runtime_wheel")
    return manifest
