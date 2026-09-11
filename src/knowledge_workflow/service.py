"""Schema-2 evidence contract; every request stays on a single generation."""
from __future__ import annotations

import base64
import json
import re
import os
import time
import uuid

from .capture import capture, validate_id
from .models import local_snapshot
from .retrieval import lexical, identifiers, body_identifier_coverage
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
        if not isinstance(query, str) or not query.strip() or len(query) > 64000:
            return error_result("invalid_query")
        if type(limit) is not int or limit < 1 or mode not in {"auto", "hybrid", "semantic", "lexical"}:
            return error_result("invalid_search_options")
        limit = min(limit, 8)
        if type(include_history) is not bool or any(x is not None and not isinstance(x, str) for x in (workspace, chip)):
            return error_result("invalid_scope")
        ambiguous = workspace == "current"
        scope = {"workspace": None if ambiguous else workspace, "chip": chip, "include_history": include_history}
        result = {"schema_version": 2, "ok": True, "query_id": uuid.uuid4().hex, "query": query,
            "mode": mode, "index_version": None,
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
            query_ids = identifiers(query)
            candidates = []
            with selected.connect() as db:
                for hit in hits:
                    row = db.execute("SELECT c.*,s.body AS parent_body,d.metadata_json,d.source_json FROM chunks c "
                        "JOIN documents d ON c.path=d.path JOIN sections s ON s.section_id=c.section_id WHERE c.id=?", (hit["rowid"],)).fetchone()
                    if row is None:
                        raise ValueError("candidate_not_in_pinned_generation")
                    if row["section_id"] in seen:
                        continue
                    seen.add(row["section_id"])
                    metadata, source = json.loads(row["metadata_json"]), json.loads(row["source_json"])
                    candidates.append({"evidence_id": selected.name + ":" + row["evidence_id"],
                        "_identifier_coverage": body_identifier_coverage(query_ids, row["parent_body"][:16000]),
                        "index_version": selected.name,
                        "path": row["path"], "heading": row["heading"], "page": row["page"],
                        "line": row["line_start"], "section_id": row["section_id"], "snippet": row["body"][:400],
                        "span": {"start": row["start_char"], "end": row["end_char"]}, "has_more": len(row["body"]) > 400,
                        "family": row["family"], "kind": row["kind"], "role": metadata.get("role"), "recalled_by": None,
                        "score": hit["score"], "source_version": source,
                        "verification": {"status": metadata.get("status"), "maturity": metadata.get("maturity"),
                            "workspace_ids": metadata.get("workspace_ids", []), "evidence_date": metadata.get("evidence_date"),
                            "level": metadata.get("evidence_level", "unspecified"), "declared_source_version": metadata.get("source_version"),
                            "current_project_verified": False, "declared": metadata.get("verification", {}),
                            "boundary": "Metadata declares scope; it is not independent revalidation."},
                        "lifecycle": metadata.get("lifecycle", metadata.get("status", "active")),
                        "maturity": metadata.get("maturity", "unspecified"), "current_project_verified": False})
                    if len(candidates) >= (40 if query_ids else limit):
                        break
            if query_ids:
                candidates.sort(key=lambda item: (-item["_identifier_coverage"], -item["score"]))
            for candidate in candidates[:limit]:
                candidate.pop("_identifier_coverage")
                candidate["source_state"] = source_state(self.config, candidate["path"], candidate["source_version"])
                result["hits"].append(candidate)
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
            with selected.connect() as db:
                siblings = db.execute("SELECT s.heading,MIN(c.evidence_id) AS evidence_id FROM sections s "
                    "JOIN chunks c ON c.section_id=s.section_id WHERE s.path=? GROUP BY s.section_id "
                    "ORDER BY abs(s.line_start-?) LIMIT 8", (row["path"], row["parent_line"])).fetchall()
            return {"schema_version": 2, "ok": True, "kb_id": self.config.kb_id, "index_version": selected.name,
                "evidence_id": evidence_id, "path": row["path"], "heading": row["heading"], "page": row["page"],
                "line": row["parent_line"], "body": body, "offset": offset,
                "focus_span": {"start": row["start_char"], "end": row["end_char"]},
                "nearby_sections": [{"heading": item["heading"], "evidence_id": selected.name + ":" + item["evidence_id"]} for item in siblings],
                "total_chars": len(text), "next_cursor": following, "source_version": source,
                "source_state": source_state(self.config, row["path"], source),
                "verification": {**metadata, "independently_verified": False}, "current_project_verified": False,
                "boundary": "Retained source text; verification declarations are not independent validation."}
        except FileNotFoundError as exc:
            return error_result("evidence_version_unavailable", str(exc))
        except Exception as exc:
            return error_result("invalid_evidence_request", str(exc))

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
        result = {"schema_version": 2, "ok": True, "kb_id": self.config.kb_id, "worker": self.worker.status(),
                  "supported_contract_versions": [2], "server_pid": os.getpid(), "cache": str(self.config.cache),
                  "knowledge_root": str(self.config.root), "feedback_queue": []}
        try:
            selected = self.store.load()
            with selected.connect() as db:
                db.execute("SELECT count(*) FROM chunks").fetchone()
            result.update(index_version=selected.name, index_state=selected.manifest["index_state"],
                source_count=selected.manifest["source_count"], chunk_count=selected.manifest["chunk_count"],
                semantic_ready=selected.manifest["semantic_ready"])
            result.update(coverage={key: selected.manifest.get(key, False if key == "token_bounded" else 0)
                                    for key in ("source_count", "chunk_count", "embedded", "token_bounded")},
                          model_profile=selected.manifest["model_profile"], model_fingerprint=selected.manifest["model_fingerprint"],
                          source_coverage=selected.manifest.get("source_coverage", {}),
                          degradation_reason=selected.manifest["model_error"])
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
