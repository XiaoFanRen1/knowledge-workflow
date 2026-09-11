"""Independent local queue owner, started outside the MCP host by the installer.

No network endpoint, executable request or pickle transport. MCP only writes the
existing bounded SQLite task records. One runner binds one knowledge configuration.
"""
import os
import subprocess
import time

import psutil

from .config import KnowledgeConfig
from .processes import command, environment
from .storage import Store
from .util import atomic_json, canonical, contained, digest, file_lock, read_json


def status(config):
    path = config.cache / "runner.json"
    try:
        state = read_json(path)
        process = psutil.Process(state["pid"])
        if abs(process.create_time() - state["created"]) >= .01 or not process.is_running():
            return {"state": "stopped"}
        if state["binding"] != digest(canonical(config.json())):
            return {"state": "configuration_changed", "pid": state["pid"]}
        return state
    except (FileNotFoundError, psutil.NoSuchProcess):
        return {"state": "stopped"}
    except (OSError, ValueError, KeyError, psutil.AccessDenied):
        return {"state": "unavailable"}


def start(config):
    current = status(config)
    if current["state"] == "ready":
        return {"ok": True, **current, "already_running": True}
    if current["state"] not in {"stopped"}:
        raise RuntimeError("maintenance_runner_requires_review")
    config.cache.mkdir(parents=True, exist_ok=True)
    with (config.cache / "runner.log").open("ab") as log:
        process = subprocess.Popen(command("runner-serve", "--config", config.config_path),
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment(),
            creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        current = status(config)
        if current["state"] == "ready":
            return {"ok": True, **current, "launcher_pid": process.pid}
        if process.poll() is not None:
            raise RuntimeError("maintenance_runner_start_failed")
        time.sleep(.05)
    raise TimeoutError("maintenance_runner_start_timeout")


def stop(config):
    from . import jobs
    store = Store(config)
    with file_lock(store.cache / "jobs.lock"):
        current = status(config)
        if current["state"] == "stopped":
            return {"ok": True, "state": "stopped"}
        with store.control() as db:
            rows = db.execute("SELECT job_id FROM jobs WHERE state NOT IN ('published','failed','cancelled','interrupted','timed_out')").fetchall()
        active = [r[0] for r in rows if jobs.status(config, r[0])["state"] not in jobs.TERMINAL]
        if active:
            return {"ok": False, "state": "busy", "job_ids": active}
        if current["state"] != "ready":
            raise RuntimeError("maintenance_runner_ownership_unconfirmed")
        current["state"] = "stopping"
        atomic_json(store.cache / "runner.json", current)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        observed = status(config)
        if observed["state"] == "stopped" and not observed.get("pid"):
            return {"ok": True, "state": "stopped"}
        time.sleep(.1)
    return {"ok": False, "state": "stopping"}


def serve(config):
    from . import jobs
    store = Store(config)
    binding = digest(canonical(config.json()))
    with file_lock(store.cache / "runner.lock"):
        state = {"state": "ready", "pid": os.getpid(), "created": psutil.Process().create_time(),
                 "kb_id": config.kb_id, "binding": binding}
        atomic_json(store.cache / "runner.json", state)
        try:
            while True:
                if read_json(store.cache / "runner.json")["state"] == "stopping":
                    break
                try:
                    with file_lock(store.cache / "jobs.lock"):
                        with store.control() as db:
                            queued = db.execute("SELECT job_id,payload FROM jobs WHERE state='queued' ORDER BY updated").fetchall()
                        for row in queued:
                            record, payload = jobs._row(store, row["job_id"])
                            if payload["cancel_requested"] or time.time() >= payload["deadline"]:
                                jobs._save(store, row["job_id"], "cancelled" if payload["cancel_requested"] else "timed_out", payload)
                                continue
                            frozen_path = contained(store.cache / "jobs", row["job_id"] + "/knowledge.json")
                            frozen = KnowledgeConfig.load(frozen_path)
                            if digest(canonical(frozen.json())) != binding:
                                payload["error"] = {"code": "knowledge_binding_changed", "message": "Queued binding differs from the runner."}
                                jobs._save(store, row["job_id"], "failed", payload)
                                continue
                            try:
                                with contained(store.cache / "jobs", row["job_id"] + "/maintenance.log").open("ab") as log:
                                    process = subprocess.Popen(command("job-supervise", "--config", frozen_path, "--job-id", row["job_id"]),
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=environment(),
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                                payload.update(launcher_pid=process.pid, launcher_created=psutil.Process(process.pid).create_time())
                                jobs._save(store, row["job_id"], "starting", payload)
                            except Exception as exc:
                                payload["error"] = {"code": type(exc).__name__, "message": str(exc)}
                                jobs._save(store, row["job_id"], "failed", payload)
                except TimeoutError:
                    # A busy publication must not retire the independent runner.
                    time.sleep(.1)
                    continue
                time.sleep(.25)
        finally:
            state["state"] = "stopped"
            atomic_json(store.cache / "runner.json", state)
    return 0
