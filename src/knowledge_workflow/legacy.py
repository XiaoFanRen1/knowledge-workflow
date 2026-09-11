"""Explicit import of schema-2 evidence snapshots; source programs stay untouched."""
import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from .storage import Store
from .util import atomic_json, canonical, contained, digest, file_lock, read_json, reject_links


def import_generation(config, database: Path, manifest_file: Path, mapping, *, publish=False):
    database, manifest_file = reject_links(database).resolve(), reject_links(manifest_file).resolve()
    manifest = read_json(manifest_file)
    name = manifest.get("generation")
    if manifest.get("schema_version") != 2 or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", name):
        raise ValueError("unsupported_legacy_generation")
    registered = {source.source_id for source in config.sources}
    if not isinstance(mapping, list) or not mapping:
        raise ValueError("explicit_source_mapping_required")
    for item in mapping:
        if item.get("source_id") not in registered or not isinstance(item.get("prefix"), str):
            raise ValueError("invalid_legacy_source_mapping")
    def remap(path):
        matches = sorted((item for item in mapping if path.startswith(item["prefix"])), key=lambda i: len(i["prefix"]), reverse=True)
        if not matches:
            raise ValueError("unmapped_legacy_source")
        result = matches[0]["source_id"] + "/" + path[len(matches[0]["prefix"]):]
        config.source_path(result)
        return result
    store = Store(config)
    destination = contained(store.generations, name)
    if database.is_relative_to(store.cache) or manifest_file.is_relative_to(store.cache):
        raise ValueError("legacy_input_must_be_independent")
    with file_lock(store.cache / "writer.lock"):
        if destination.exists():
            raise FileExistsError("generation_identifier_already_present")
        source_names = list(manifest["sources"])
        names = {name: remap(name) for name in source_names}
        if len(set(names.values())) != len(names):
            raise ValueError("legacy_source_mapping_collision")
        destination.mkdir(parents=True)
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(destination / "vault.sqlite")) as target:
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("legacy_database_integrity_failed")
                paths = {row[0] for row in target.execute("SELECT path FROM documents")}
                if paths != set(names):
                    raise ValueError("legacy_manifest_source_mismatch")
                for old, new in names.items():
                    for table in ("documents", "sections", "chunks"):
                        target.execute(f"UPDATE {table} SET path=? WHERE path=?", (new, old))
                target.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
                target.commit()
        updated = {**manifest, "kb_id": config.kb_id, "sources": {names[k]: v for k, v in manifest["sources"].items()},
            "index_state": "ready" if manifest["chunk_count"] else "empty",
            "import_origin": {"generation": name, "manifest_sha256": digest(manifest_file.read_bytes()),
                              "paths_remapped": True, "evidence_dates_renewed": False}}
        atomic_json(destination / "manifest.json", updated)
        selected = store.load(name)
        store.validate(selected)
        if publish:
            previous = store.load().name
            store.publish(selected, expected_previous=previous)
    return {"ok": True, "generation": name, "sources": len(names), "published": publish,
            "boundary": "Imported retained evidence; original files and verification dates were not changed."}


def import_feedback(config, database):
    database = reject_links(Path(database)).resolve()
    store = Store(config)
    if database.is_relative_to(store.cache):
        raise ValueError("legacy_feedback_input_must_be_independent")
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
        rows = source.execute("SELECT event_id,created,query_id,outcome,evidence_id,reason,detail_json FROM feedback").fetchall()
    with store.control(write=True) as target:
        for row in rows:
            if row[4]:
                generation, _, evidence = row[4].partition(":")
                with store.load(generation).connect() as db:
                    if not db.execute("SELECT 1 FROM chunks WHERE evidence_id=?", (evidence,)).fetchone():
                        raise ValueError("legacy_feedback_evidence_unavailable")
            old = target.execute("SELECT * FROM feedback WHERE event_id=?", (row[0],)).fetchone()
            if old and tuple(old) != row:
                raise ValueError("legacy_feedback_event_conflict")
            if not old:
                target.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?,?)", row)
    return {"ok": True, "feedback_events": len(rows), "maturity_changed": False}
