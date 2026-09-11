"""Local snapshots with consistent SQLite and explicit restored source bindings."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path

from .config import KnowledgeConfig, SourceRoot
from .content import inventory
from .storage import Store
from .util import atomic_bytes, atomic_json, canonical, contained, digest, file_lock, read_json, reject_links


def _sqlite_backup(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("snapshot_database_integrity_failed")


def backup(config, destination: Path):
    destination = reject_links(destination).resolve()
    for root in (config.root, config.cache, config.model_dir, *(s.path for s in config.sources)):
        if destination == root or destination.is_relative_to(root) or root.is_relative_to(destination):
            raise ValueError("snapshot_must_be_outside_source_and_runtime_roots")
    if destination.exists():
        raise FileExistsError("snapshot_destination_exists")
    store = Store(config)
    manifest = {"schema_version": 1, "snapshot_id": uuid.uuid4().hex, "complete": False,
                "kb_id": config.kb_id, "created_unix": time.time(), "files": {}}
    destination.mkdir(parents=True)
    atomic_json(destination / "snapshot.json", manifest)
    with file_lock(store.cache / "capture.lock"), file_lock(store.cache / "writer.lock"):
        pointer = read_json(store.current)
        generations = {pointer["generation"]}
        if pointer.get("previous"):
            generations.add(pointer["previous"])
        _sqlite_backup(store.cache / "control.sqlite", destination / "index/control.sqlite")
        with closing(sqlite3.connect(destination / "index/control.sqlite")) as db:
            feedback = db.execute("SELECT evidence_id FROM feedback WHERE evidence_id!=''").fetchall()
            for (evidence_id,) in feedback:
                generations.add(evidence_id.split(":", 1)[0])
            logical_feedback = db.execute("SELECT * FROM feedback ORDER BY event_id").fetchall()
            manifest["feedback_logic_sha256"] = digest(canonical(logical_feedback))
        atomic_json(destination / "knowledge.json", config.json())
        atomic_json(destination / "index/current.json", pointer)
        for name in sorted(generations):
            selected = store.load(name)
            store.validate(selected)
            target = contained(destination, "index/generations/" + name)
            _sqlite_backup(selected.sqlite, target / "vault.sqlite")
            atomic_json(target / "manifest.json", selected.manifest)
        for relative, path in inventory(config):
            before = path.stat()
            raw = path.read_bytes()
            after = path.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError("snapshot_source_changed_during_read")
            atomic_bytes(contained(destination, "sources/" + relative), raw)
        for path in sorted(destination.rglob("*")):
            if path.is_file() and path.relative_to(destination).as_posix() != "snapshot.json":
                relative = path.relative_to(destination).as_posix()
                manifest["files"][relative] = {"sha256": digest(path.read_bytes()), "bytes": path.stat().st_size}
        manifest.update(complete=True, generations=sorted(generations),
                        model_dependency={"included": False, "profile": store.load().manifest["model_profile"]})
        atomic_json(destination / "snapshot.json", manifest)
    return {"ok": True, "snapshot_id": manifest["snapshot_id"], "files": len(manifest["files"]),
            "generations": len(generations), "model_included": False, "path": str(destination)}


def verify(snapshot: Path):
    snapshot = reject_links(snapshot).resolve()
    manifest = read_json(snapshot / "snapshot.json")
    if manifest.get("schema_version") != 1 or manifest.get("complete") is not True:
        raise ValueError("snapshot_not_complete")
    actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*")
              if p.is_file() and p.relative_to(snapshot).as_posix() != "snapshot.json"}
    if actual != set(manifest["files"]):
        raise ValueError("snapshot_file_inventory_mismatch")
    for relative, expected in manifest["files"].items():
        path = contained(snapshot, relative)
        if path.stat().st_size != expected["bytes"] or digest(path.read_bytes()) != expected["sha256"]:
            raise ValueError("snapshot_hash_mismatch: " + relative)
        if path.suffix == ".sqlite":
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("snapshot_database_integrity_failed")
    return manifest


def restore(snapshot: Path, destination: Path, model_dir: Path):
    snapshot, destination = reject_links(snapshot).resolve(), reject_links(destination).resolve()
    manifest = verify(snapshot)
    if destination.exists() or snapshot == destination or snapshot.is_relative_to(destination) or destination.is_relative_to(snapshot):
        raise ValueError("restore_requires_new_independent_directory")
    previous = read_json(snapshot / "knowledge.json")
    root = destination / "knowledge"
    cache = root / ".index"
    sources = []
    for source in previous["sources"]:
        target = root / "notes" if source["id"] == "notes" else destination / "sources" / source["id"]
        sources.append(SourceRoot(source["id"], target, tuple(source["suffixes"])))
    config = KnowledgeConfig(root / "knowledge.json", manifest["kb_id"], root, cache,
                             reject_links(model_dir).resolve(), tuple(sources), previous["cpu_threads"])
    for source in sources:
        source.path.mkdir(parents=True, exist_ok=True)
    for relative in manifest["files"]:
        if relative.startswith("index/"):
            target = contained(cache, relative[len("index/"):])
        elif relative.startswith("sources/"):
            target = config.source_path(relative[len("sources/"):])
        else:
            continue
        atomic_bytes(target, contained(snapshot, relative).read_bytes())
    atomic_json(config.config_path, config.json())
    config = KnowledgeConfig.load(config.config_path)
    store = Store(config)
    with store.control(write=True) as db:
        feedback = db.execute("SELECT * FROM feedback ORDER BY event_id").fetchall()
        if digest(canonical([tuple(row) for row in feedback])) != manifest["feedback_logic_sha256"]:
            raise ValueError("restored_feedback_logic_mismatch")
        # A restored task must never act on original-machine process identities or paths.
        for row in db.execute("SELECT job_id,state,payload FROM jobs").fetchall():
            payload = json.loads(row["payload"])
            for key in list(payload):
                if key.endswith(("_pid", "_created")) or key in {"log", "config_snapshot"}:
                    payload.pop(key)
            state = row["state"] if row["state"] in {"published", "failed", "cancelled", "interrupted", "timed_out"} else "interrupted"
            payload["restored_from_snapshot"] = manifest["snapshot_id"]
            db.execute("UPDATE jobs SET state=?,payload=? WHERE job_id=?", (state, canonical(payload), row["job_id"]))
    for name in manifest["generations"]:
        store.validate(store.load(name))
    from .service import KnowledgeService
    service = KnowledgeService(config)
    try:
        count = 0
        with store.control() as db:
            for row in db.execute("SELECT evidence_id FROM feedback WHERE evidence_id!=''"):
                evidence = service.read_evidence(row[0])
                if not evidence["ok"] or not evidence["body"]:
                    raise ValueError("restored_feedback_evidence_unreadable")
                count += 1
    finally:
        service.close()
    result = {"ok": True, "snapshot_id": manifest["snapshot_id"], "config": str(config.config_path),
              "files_verified": len(manifest["files"]), "feedback_evidence_read": count,
              "boundary": "Local isolated restore; model files are supplied separately."}
    atomic_json(destination / "restore-report.json", result)
    return result
