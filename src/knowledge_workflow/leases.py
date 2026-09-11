"""Runtime ownership records used for deferred version cleanup."""
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

from .process_identity import creation_time

from .util import atomic_json, read_json


@contextmanager
def runtime_lease(config, kind):
    entry = Path(__file__).with_name("entry.py").resolve()
    lease = config.cache / "runtime-leases" / (uuid.uuid4().hex + ".json")
    value = {"pid": os.getpid(), "created": creation_time(), "kind": kind,
             "entry": str(entry), "kb_id": config.kb_id}
    atomic_json(lease, value)
    try:
        yield
    finally:
        if lease.exists() and read_json(lease) == value:
            lease.unlink()
