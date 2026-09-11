"""Schema-2 evidence contract; every request stays on a single generation."""
from __future__ import annotations

import base64
import json
import re
import time
import uuid

from .capture import capture, validate_id
from .models import local_snapshot
from .retrieval import lexical
from .storage import Store
from .util import canonical, digest
from .worker import ModelWorker


def error_result(code, detail=""):
    return {"schema_version": 2, "ok": False, "error": {"code": code, "message": detail or code}}


def source_state(config, path, source):
    try:
        raw = config.source_path(path).read_bytes()
        if digest(raw) == source["sha256"]:
            return "unchanged"
        if source["kind"] != "pdf" and digest(raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")) == source["text_sha256"]:
            return "byte_changed_text_unchanged"
        return "changed_since_index"
    except FileNotFoundError:
        return "source_removed"
    except (OSError, ValueError):
        return "source_unreadable"


class KnowledgeService:
    def __init__(self, config, *, worker=None):
        self.config = config
        self.store = Store(config)
        self.worker = worker or ModelWorker(config)

    def close(self):
        self.worker.close()

    def search(self, query, limit=8, mode="auto", workspace=None, chip=None, include_history=False):
        started = time.perf_counter()
        if not isinstance(query, str) or not query.strip() or len(query) > 4000:
            return error_result("invalid_query")
        if type(limit) is not int or not 1 <= limit <= 50 or mode not in {"auto", "semantic", "lexical"}:
            return error_result("invalid_search_options")
        if type(include_history) is not bool or any(x is not None and not isinstance(x, str) for x in (workspace, chip)):
            return error_result("invalid_scope")
        ambiguous = workspace == "current"
        scope = {"workspace": None if ambiguous else workspace, "chip": chip, "include_history": include_history}
        result = {"schema_version": 2, "ok": True, "query_id": uuid.uuid4().hex, "query": query,
            "kb_id": self.config.kb_id, "hits": [], "degraded": False, "semantic_cold_start": False,
            "scope": {**scope, "state": "explicit" if scope["workspace"] else "library_only",
                      "current_project_verified": False}}
        try:
            selected = self.store.load()
            result["index_version"] = selected.name
            hits = lexical(selected, query, scope)
            stage = {"status": "skipped", "reason": "lexical_mode"}
            if mode != "lexical" and selected.manifest["chunk_count"]:
                if not selected.manifest["semantic_ready"]:
                    stage = {"status": "unavailable", "reason": selected.manifest["model_error"]}
                else:
                    try:
                        state = self.worker.status()
                        result["semantic_cold_start"] = state["phase"] != "ready" or state["generation"] != selected.name
                        reply = self.worker.call({"generation": selected.name,
                            "fingerprint": selected.manifest["model_fingerprint"], "query": query,
                            "scope": scope, "mode": mode}, selected.manifest["model_fingerprint"])
                        hits = reply["hits"]
                        stage = {"status": "used", "reason": "", "runtime": reply.get("runtime", {})}
                    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
                        stage = {"status": "failed", "reason": str(exc), "fallback": "lexical"}
            elif not selected.manifest["chunk_count"]:
                stage = {"status": "skipped", "reason": "empty_knowledge"}
            result["stages"] = {"semantic": stage, "rerank": {"status": "skipped", "reason": "not_configured"}}
            result["degraded"] = stage["status"] in {"failed", "unavailable"}
            seen = set()
            with selected.connect() as db:
                for hit in hits:
                    row = db.execute("SELECT c.*,d.metadata_json,d.source_json FROM chunks c "
                        "JOIN documents d ON c.path=d.path WHERE c.id=?", (hit["rowid"],)).fetchone()
                    if row is None:
                        raise ValueError("candidate_not_in_pinned_generation")
                    if row["section_id"] in seen:
                        continue
                    seen.add(row["section_id"])
                    metadata, source = json.loads(row["metadata_json"]), json.loads(row["source_json"])
                    result["hits"].append({"evidence_id": selected.name + ":" + row["evidence_id"],
                        "path": row["path"], "heading": row["heading"], "page": row["page"],
                        "line": row["line_start"], "section_id": row["section_id"], "snippet": row["body"][:1200],
                        "score": hit["score"], "source_version": source,
                        "source_state": source_state(self.config, row["path"], source),
                        "verification": metadata.get("verification", {"evidence_level": metadata.get("evidence_level", "unspecified")}),
                        "lifecycle": metadata.get("lifecycle", metadata.get("status", "active")),
                        "maturity": metadata.get("maturity", "unspecified"), "current_project_verified": False})
                    if len(result["hits"]) >= limit:
                        break
            result["result_state"] = "weak" if result["hits"] and stage["status"] == "used" else "candidates" if result["hits"] else "empty"
            if ambiguous:
                result["result_state"] = "scope_ambiguous"
            if not result["hits"] and stage["status"] == "failed":
                result.update(ok=False, result_state="error", error={"code": "retrieval_failed", "message": stage["reason"]})
        except Exception as exc:
            result.update(ok=False, result_state="error", error={"code": type(exc).__name__, "message": str(exc)})
        result["timing"] = {"core_ms": round((time.perf_counter() - started) * 1000, 3)}
        return result

    def read_evidence(self, evidence_id, cursor=None, max_chars=6000):
        try:
            match = re.fullmatch(r"([a-zA-Z0-9_-]{1,96}):([a-f0-9]{64})", evidence_id)
            if not match or type(max_chars) is not int or not 1 <= max_chars <= 6000:
                raise ValueError("invalid_evidence_request")
            selected = self.store.load(match[1])
            with selected.connect() as db:
                row = db.execute("SELECT c.*,s.body AS parent_body,s.line_start AS parent_line,d.source_json,d.metadata_json "
                    "FROM chunks c JOIN sections s ON s.section_id=c.section_id JOIN documents d ON d.path=c.path "
                    "WHERE c.evidence_id=?", (match[2],)).fetchone()
            if row is None:
                return error_result("evidence_not_found")
            offset = 0
            if cursor:
                if not isinstance(cursor, str) or len(cursor) > 2048:
                    raise ValueError("invalid_cursor")
                value = json.loads(base64.urlsafe_b64decode(cursor).decode("utf-8"))
                if value.get("evidence_id") != evidence_id or type(value.get("offset")) is not int:
                    raise ValueError("cursor_evidence_mismatch")
                offset = value["offset"]
            text = row["parent_body"]
            if not 0 <= offset <= len(text):
                raise ValueError("cursor_out_of_range")
            body = text[offset:offset + max_chars]
            following = None
            if offset + len(body) < len(text):
                following = base64.urlsafe_b64encode(canonical({"evidence_id": evidence_id,
                    "offset": offset + len(body)}).encode()).decode()
            source, metadata = json.loads(row["source_json"]), json.loads(row["metadata_json"])
            return {"schema_version": 2, "ok": True, "kb_id": self.config.kb_id, "index_version": selected.name,
                "evidence_id": evidence_id, "path": row["path"], "heading": row["heading"], "page": row["page"],
                "line": row["parent_line"] + text[:offset].count("\n"), "body": body, "offset": offset,
                "total_chars": len(text), "next_cursor": following, "source_version": source,
                "source_state": source_state(self.config, row["path"], source),
                "verification": metadata.get("verification", {}), "current_project_verified": False,
                "boundary": "Retained source text; verification declarations are not independent validation."}
        except Exception as exc:
            return error_result(type(exc).__name__, str(exc))

    def record_feedback(self, event_id, query_id, outcome, reason="", evidence_id=""):
        try:
            validate_id(event_id)
            validate_id(query_id)
            if outcome not in {"useful", "partial", "irrelevant", "missing", "stale", "conflict"} or len(reason) > 4000:
                raise ValueError("invalid_feedback")
            if evidence_id and not self.read_evidence(evidence_id, max_chars=1)["ok"]:
                raise ValueError("invalid_feedback_evidence")
            event = canonical({"query_id": query_id, "outcome": outcome, "reason": reason, "evidence_id": evidence_id})
            with self.store.control(write=True) as db:
                old = db.execute("SELECT detail_json FROM feedback WHERE event_id=?", (event_id,)).fetchone()
                if old and old[0] != event:
                    raise ValueError("feedback_event_id_conflict")
                if not old:
                    db.execute("INSERT INTO feedback VALUES(?,?,?,?,?,?,?)", (event_id, time.time(), query_id, outcome, evidence_id, reason, event))
            return {"schema_version": 2, "ok": True, "event_id": event_id, "recorded": True,
                    "duplicate": bool(old), "maturity_changed": False}
        except Exception as exc:
            return error_result(type(exc).__name__, str(exc))

    def capture_knowledge(self, **request):
        return capture(self.config, **request)

    def status(self):
        result = {"schema_version": 2, "ok": True, "kb_id": self.config.kb_id, "worker": self.worker.status()}
        try:
            selected = self.store.load()
            with selected.connect() as db:
                db.execute("SELECT count(*) FROM chunks").fetchone()
            result.update(index_version=selected.name, index_state=selected.manifest["index_state"],
                source_count=selected.manifest["source_count"], chunk_count=selected.manifest["chunk_count"],
                semantic_ready=selected.manifest["semantic_ready"])
            with self.store.control() as db:
                result["feedback_count"] = db.execute("SELECT count(*) FROM feedback").fetchone()[0]
                records = db.execute("SELECT record_id,sha256 FROM records").fetchall()
                result["pending_capture_count"] = sum(selected.manifest["sources"].get("notes/" + row["record_id"] + ".md", {}).get("sha256") != row["sha256"] for row in records)
                result["maintenance"] = [{"job_id": row["job_id"], "state": row["state"]}
                    for row in db.execute("SELECT job_id,state FROM jobs ORDER BY updated DESC LIMIT 10")]
        except Exception as exc:
            result.update(ok=False, index_state="unavailable", error={"code": type(exc).__name__, "message": str(exc)})
        try:
            local_snapshot(self.config.model_dir)
            result["model_files"] = "present"
        except (OSError, ValueError):
            result["model_files"] = "unavailable"
        return result
