"""Recoverable, idempotent writes to managed notes only."""
from __future__ import annotations

import json
import re
import time
import uuid

from .storage import Store
from .util import atomic_bytes, canonical, contained, digest, file_lock


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError("invalid_operation_identifier")


def _finish(store, row):
    payload = json.loads(row["payload"])
    target = contained(store.config.notes, row["record_id"] + ".md")
    current = digest(target.read_bytes()) if target.exists() else None
    if current != row["target_hash"]:
        if current != row["previous_hash"]:
            raise ValueError("capture_content_conflict")
        atomic_bytes(target, payload["document"].encode("utf-8"))
    with store.control(write=True) as db:
        db.execute("INSERT OR REPLACE INTO records VALUES(?,?,?)",
                   (row["record_id"], row["operation_id"], row["target_hash"]))
        db.execute("UPDATE captures SET state='saved' WHERE operation_id=?", (row["operation_id"],))
    return target


def capture(config, operation_id, title, body, source_refs=None, scope=None,
            verification=None, record_id=None, expected_hash=None):
    validate_id(operation_id)
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 300 or "\n" in title:
        raise ValueError("invalid_title")
    if not isinstance(body, str) or not body.strip() or len(body.encode("utf-8")) > 1024 * 1024:
        raise ValueError("invalid_body")
    if record_id is not None and not re.fullmatch(r"[a-f0-9]{32}", record_id):
        raise ValueError("invalid_record_identifier")
    if record_id and not re.fullmatch(r"[a-f0-9]{64}", expected_hash or ""):
        raise ValueError("expected_hash_required")
    if not isinstance(source_refs or [], list) or not isinstance(scope or [], list):
        raise ValueError("source_refs_and_scope_require_lists")
    if verification is not None and not isinstance(verification, dict):
        raise ValueError("verification_requires_mapping")
    request = {"title": title, "body": body, "source_refs": source_refs or [], "scope": scope or [],
               "verification": verification or {}, "record_id": record_id, "expected_hash": expected_hash}
    request_hash = digest(canonical(request))
    store = Store(config)
    duplicate = False
    with file_lock(store.cache / "capture.lock"):
        with store.control() as db:
            old = db.execute("SELECT * FROM captures WHERE operation_id=?", (operation_id,)).fetchone()
        if old:
            if old["request_hash"] != request_hash:
                raise ValueError("capture_operation_id_conflict")
            row, duplicate = dict(old), True
        else:
            rid = record_id or uuid.uuid4().hex
            target = contained(config.notes, rid + ".md")
            previous = digest(target.read_bytes()) if target.exists() else None
            if record_id:
                with store.control() as db:
                    owned = db.execute("SELECT * FROM records WHERE record_id=?", (rid,)).fetchone()
                    pending = db.execute("SELECT 1 FROM captures WHERE record_id=? AND state='prepared'", (rid,)).fetchone()
                if not owned or previous != expected_hash:
                    raise ValueError("capture_content_conflict")
                if pending:
                    raise ValueError("capture_recovery_required")
            elif previous is not None:
                raise ValueError("capture_target_collision")
            # JSON objects are a strict subset of YAML; safe loaders preserve these fields.
            metadata = {"record_id": rid, "status": "active", "maturity": "unverified",
                "role": "knowledge", "workspace_ids": scope or [], "source_refs": source_refs or [],
                "verification": {"declared": verification or {}, "independently_verified": False}}
            document = "---\n" + canonical(metadata) + "\n---\n\n# " + title.strip() + "\n\n" + body.rstrip() + "\n"
            row = {"operation_id": operation_id, "request_hash": request_hash, "record_id": rid,
                   "target_hash": digest(document), "previous_hash": previous,
                   "payload": canonical({"document": document}), "state": "prepared", "created": time.time()}
            with store.control(write=True) as db:
                db.execute("INSERT INTO captures VALUES(?,?,?,?,?,?,?,?)", tuple(row.values()))
        # A completed retry reports drift without rewriting a user's later edit.
        target = contained(config.notes, row["record_id"] + ".md")
        if row["state"] != "saved":
            target = _finish(store, row)
        current = digest(target.read_bytes()) if target.exists() else None
        selected = store.load()
        source_key = "notes/" + target.name
        indexed = selected.manifest["sources"].get(source_key, {}).get("sha256") == row["target_hash"]
    return {"schema_version": 2, "ok": True, "operation_id": operation_id, "record_id": row["record_id"],
        "path": str(target), "sha256": row["target_hash"], "state": "saved", "duplicate": duplicate,
        "source_changed_after_capture": current != row["target_hash"],
        "needs_maintenance": not indexed, "index_version": selected.name if indexed else None,
        "verification_state": "declared_only"}
