"""Bounded import compatibility for the disposable model worker.

CPython 3.14.2 checks every RECORD file while inferring top-level package names.
Transformers 5.0 performs that scan at import. One extant witness per name is
sufficient for the same mapping; no package metadata is cached across requests.
This context must only be used during startup in the isolated model process.
"""
from __future__ import annotations

import csv
import importlib.metadata as metadata
import inspect
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path


def _top_name(path):
    top, *rest = path.parts
    return top if rest else inspect.getmodulename(path) or str(path)


def _inferred_packages(dist, stats):
    record = dist.read_text("RECORD")
    if (type(dist) is not metadata.PathDistribution
            or not isinstance(dist.locate_file(""), Path) or not record):
        stats["fallback_distributions"] += 1
        return {_top_name(p) for p in dist.files or () if "." not in _top_name(p)}
    rows = list(csv.reader(record.splitlines()))
    # Preserve the standard provider's behavior for malformed or unusual records.
    try:
        for row in rows:
            if not 1 <= len(row) <= 3 or not row[0]:
                raise ValueError("invalid_record")
            if len(row) > 1 and row[1] and "=" not in row[1]:
                raise ValueError("invalid_hash")
            if len(row) > 2 and row[2]:
                int(row[2])
    except ValueError:
        stats["fallback_distributions"] += 1
        return {_top_name(p) for p in dist.files or () if "." not in _top_name(p)}
    found = set()
    for row in rows:
        path = metadata.PackagePath(row[0])
        name = _top_name(path)
        if "." in name or name in found:
            continue
        stats["exists_calls"] += 1
        if dist.locate_file(path).exists():
            found.add(name)
    return found


def packages_distributions(stats):
    """Same mapping as CPython 3.14.2; preserve namespace distribution order."""
    result = {}
    for dist in metadata.distributions():
        stats["distributions"] += 1
        names = (dist.read_text("top_level.txt") or "").split() or _inferred_packages(dist, stats)
        for name in names:
            result.setdefault(name, []).append(dist.metadata["Name"])
    return result


@contextmanager
def model_imports():
    """Temporarily adapt one known runtime; leave every other runtime standard."""
    state = {"metadata_scan": "standard", "reason": "runtime_not_targeted"}
    if "transformers" in sys.modules:
        state.update(metadata_scan="already_imported", reason="")
        yield state
        return
    if os.environ.get("KB_STANDARD_IMPORT_SCAN") == "1":
        state["reason"] = "explicit_standard_scan"
        yield state
        return
    if os.name != "nt" or sys.implementation.name != "cpython" or sys.version_info[:3] != (3, 14, 2):
        yield state
        return
    try:
        supported = metadata.version("transformers") == "5.0.0"
    except metadata.PackageNotFoundError:
        supported = False
    if not supported:
        yield state
        return
    state.update(metadata_scan="record_witness_v1", reason="", distributions=0,
                 exists_calls=0, fallback_distributions=0, metadata_ms=0.0)
    original = metadata.packages_distributions

    def scan():
        started = time.perf_counter()
        try:
            return packages_distributions(state)
        finally:
            state["metadata_ms"] += round((time.perf_counter() - started) * 1000, 3)
            print(json.dumps({"event": "model_metadata_scan", **state}), file=sys.stderr, flush=True)

    metadata.packages_distributions = scan
    try:
        yield state
    finally:
        metadata.packages_distributions = original
