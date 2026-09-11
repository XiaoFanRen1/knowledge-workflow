"""Durable maintenance operations supervised independently of MCP connections."""
from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from .capture import validate_id
from .config import KnowledgeConfig
from .processes import command, environment
from .owned_process import track, terminate as terminate_process
from .storage import Store
from .util import atomic_json, canonical, contained, digest, file_lock, read_json

TERMINAL = {"published", "failed", "cancelled", "interrupted", "timed_out"}


def _row(store, job_id):
    if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
        raise ValueError("invalid_job_identifier")
    with store.control() as db:
        row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise ValueError("unknown_maintenance_job")
    return dict(row), json.loads(row["payload"])


def _save(store, job_id, state, payload):
    with store.control(write=True) as db:
        db.execute("UPDATE jobs SET state=?,payload=?,updated=? WHERE job_id=?",
                   (state, canonical(payload), time.time(), job_id))


def _identity(pid, created):
    import psutil
    try:
        process = psutil.Process(pid)
        return process.is_running() and abs(process.create_time() - created) < .01
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return None


def status(config, job_id):
    store = Store(config)
    row, payload = _row(store, job_id)
    state = row["state"]
    if state not in TERMINAL:
        pointer = read_json(store.current)
        if pointer.get("job_id") == job_id:
            state = "published"
            payload = {**payload, "generation": pointer["generation"]}
        elif payload.get("supervisor_pid") or payload.get("launcher_pid") or payload.get("runner_pid"):
            kind = "supervisor" if payload.get("supervisor_pid") else "launcher" if payload.get("launcher_pid") else "runner"
            alive = _identity(payload[kind + "_pid"], payload[kind + "_created"])
            if alive is False:
                state = "interrupted"
            elif alive is None:
                payload = {**payload, "ownership_check": "unavailable"}
        elif time.time() - payload["submitted"] > 10:
            state = "interrupted"
    return {"schema_version": 2, "ok": True, "kb_id": config.kb_id,
            "job_id": job_id, "operation_id": row["operation_id"], "state": state, **payload}


def submit(config, operation_id, *, timeout_seconds=600, lexical_only=False):
    validate_id(operation_id)
    if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 86400:
        raise ValueError("invalid_maintenance_deadline")
    store = Store(config)
    request_hash = digest(canonical({"config": config.json(), "timeout": timeout_seconds, "lexical_only": lexical_only}))
    with file_lock(store.cache / "jobs.lock"):
        with store.control() as db:
            old = db.execute("SELECT * FROM jobs WHERE operation_id=?", (operation_id,)).fetchone()
            active = db.execute("SELECT * FROM jobs WHERE state NOT IN ('published','failed','cancelled','interrupted','timed_out')").fetchall()
        if old:
            if old["request_hash"] != request_hash:
                raise ValueError("maintenance_operation_id_conflict")
            return {**status(config, old["job_id"]), "duplicate": True}
        for row in active:
            observed = status(config, row["job_id"])
            if observed["state"] not in TERMINAL:
                return {"schema_version": 2, "ok": False, "error": {"code": "maintenance_busy", "message": "An existing task owns this knowledge writer."},
                        "job_id": row["job_id"]}
            _save(store, row["job_id"], observed["state"], json.loads(row["payload"]))
        from .runner import status as runner_status
        runner = runner_status(config)
        if runner["state"] != "ready":
            raise RuntimeError("maintenance_runner_unavailable: start the installed runner from your terminal")
        job_id = uuid.uuid4().hex
        directory = contained(store.cache / "jobs", job_id)
        directory.mkdir(parents=True)
        frozen = directory / "knowledge.json"
        atomic_json(frozen, config.json())
        payload = {"submitted": time.time(), "deadline": time.time() + timeout_seconds,
                   "lexical_only": bool(lexical_only), "cancel_requested": False,
                   "config_snapshot": str(frozen), "log": str(directory / "maintenance.log"),
                   "runner_pid": runner["pid"], "runner_created": runner["created"]}
        with store.control(write=True) as db:
            db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?)",
                       (job_id, operation_id, request_hash, "queued", canonical(payload), time.time()))
    return {**status(config, job_id), "duplicate": False}


def cancel(config, job_id):
    store = Store(config)
    with file_lock(store.cache / "jobs.lock"):
        observed = status(config, job_id)
        if observed["state"] in TERMINAL:
            return observed
        row, payload = _row(store, job_id)
        payload["cancel_requested"] = True
        _save(store, job_id, row["state"], payload)
    return status(config, job_id)


def supervise(config, job_id):
    import psutil
    store = Store(config)
    child = None
    try:
        with file_lock(store.cache / "jobs.lock"):
            row, payload = _row(store, job_id)
            if row["state"] in TERMINAL:
                return 1
            # Windows venv launchers and the running interpreter have different PIDs.
            payload.update(supervisor_pid=os.getpid(), supervisor_created=psutil.Process().create_time())
            if payload["cancel_requested"]:
                _save(store, job_id, "cancelled", payload)
                return 0
            child = subprocess.Popen(command("job-build", "--config", config.config_path, "--job-id", job_id),
                stdin=subprocess.DEVNULL, env=environment(),
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            track(child)
            payload.update(builder_pid=child.pid, builder_created=psutil.Process(child.pid).create_time())
            _save(store, job_id, "running", payload)
        while child.poll() is None:
            row, payload = _row(store, job_id)
            reason = "cancelled" if payload["cancel_requested"] else "timed_out" if time.time() >= payload["deadline"] else None
            if reason:
                with file_lock(store.cache / "jobs.lock"):
                    if read_json(store.current).get("job_id") == job_id:
                        reason = "published"
                    else:
                        # This Popen handle belongs to this supervisor; never kill a PID supplied by a client.
                        terminate_process(child)
                    row, payload = _row(store, job_id)
                    _save(store, job_id, reason, payload)
                break
            time.sleep(.1)
        child.wait(timeout=5)
        with file_lock(store.cache / "jobs.lock"):
            row, payload = _row(store, job_id)
            observed = status(config, job_id)
            if observed["state"] == "published":
                payload["generation"] = read_json(store.current)["generation"]
                _save(store, job_id, "published", payload)
            elif row["state"] not in TERMINAL:
                payload["exit_code"] = child.returncode
                _save(store, job_id, "failed", payload)
    except BaseException as exc:
        if child is not None and child.poll() is None:
            terminate_process(child)
        with file_lock(store.cache / "jobs.lock"):
            row, payload = _row(store, job_id)
            payload["error"] = {"code": type(exc).__name__, "message": str(exc)}
            _save(store, job_id, "interrupted", payload)
        raise
    return 0


def execute_build(config, job_id):
    from .build import build
    store = Store(config)
    row, payload = _row(store, job_id)

    def publication(store, generation, previous):
        with file_lock(store.cache / "jobs.lock"):
            row, current = _row(store, job_id)
            if row["state"] not in {"starting", "running"} or current["cancel_requested"]:
                raise RuntimeError("maintenance_cancelled_before_publication")
            if time.time() >= current["deadline"]:
                raise TimeoutError("maintenance_deadline_exceeded")
            current["candidate_generation"] = generation.name
            _save(store, job_id, "running", current)
            store.publish(generation, expected_previous=previous, job_id=job_id)
            current.update(generation=generation.name, result={k: v for k, v in generation.manifest.items() if k != "sources"})
            _save(store, job_id, "published", current)

    try:
        build(config, lexical_only=payload["lexical_only"], publication=publication, job_id=job_id)
    except Exception as exc:
        with file_lock(store.cache / "jobs.lock"):
            row, current = _row(store, job_id)
            current["error"] = {"code": type(exc).__name__, "message": str(exc)}
            if read_json(store.current).get("job_id") != job_id:
                _save(store, job_id, "failed", current)
        raise
