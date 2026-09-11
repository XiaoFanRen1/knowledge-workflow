"""Retain process identities across Windows venv launcher/interpreter boundaries."""
import psutil


def track(process):
    process.kw_identities = {}
    try:
        process.kw_identities[process.pid] = psutil.Process(process.pid).create_time()
    except psutil.NoSuchProcess:
        pass
    return process


def remember(process, pid, created):
    try:
        candidate = psutil.Process(pid)
        if abs(candidate.create_time() - created) >= .01:
            raise RuntimeError("worker_process_identity_mismatch")
        if pid != process.pid and process.pid not in {parent.pid for parent in candidate.parents()}:
            raise RuntimeError("worker_process_is_not_owned")
    except psutil.Error as exc:
        raise RuntimeError("worker_process_identity_unavailable") from exc
    process.kw_identities[pid] = created


def terminate(process):
    identities = dict(getattr(process, "kw_identities", {}))
    try:
        parent = psutil.Process(process.pid)
        if process.pid in identities and abs(parent.create_time() - identities[process.pid]) < .01:
            for child in parent.children(recursive=True):
                identities[child.pid] = child.create_time()
    except psutil.NoSuchProcess:
        pass
    # Stop children before their redirector, so the original parent identity remains checkable.
    ordered = [item for item in identities.items() if item[0] != process.pid]
    ordered += [item for item in identities.items() if item[0] == process.pid]
    for pid, created in ordered:
        try:
            candidate = psutil.Process(pid)
            if abs(candidate.create_time() - created) >= .01:
                continue
            candidate.terminate()
            try:
                candidate.wait(timeout=2)
            except psutil.TimeoutExpired:
                candidate.kill()
                candidate.wait(timeout=2)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            raise RuntimeError("owned_process_cleanup_unconfirmed") from exc
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=3)
