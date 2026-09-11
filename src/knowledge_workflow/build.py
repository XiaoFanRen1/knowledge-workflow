"""Build and validate before atomic publication; reuse only required vector keys."""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from contextlib import closing

from .content import chunk_documents, collect_documents
from .models import LocalBackend, embedding_fingerprint, fingerprint, profile
from .storage import SCHEMA, Store
from .util import atomic_json, canonical, digest, file_lock, read_json


def recover_vectors(store, cache, required, model_key, dimension):
    missing = set(required)
    keys = sorted(required)
    for offset in range(0, len(keys), 400):
        batch = keys[offset:offset + 400]
        for key, dim, size in cache.execute(
            f"SELECT key,dim,length(blob) FROM vectors WHERE key IN ({','.join('?' for _ in batch)})", batch):
            if dim == dimension and size == dimension * 4:
                missing.discard(key)
    metrics = {"required_keys": len(required), "missing_before": len(missing),
               "restored": 0, "history_opened": 0, "invalid_history_vectors": 0, "encoded": 0}
    paths = sorted(store.generations.glob("*/manifest.json"), reverse=True) if missing else []
    for path in paths:
        if not missing:
            break
        try:
            generation = store.load(path.parent.name)
            spec = generation.manifest["model_profile"]
            if not generation.manifest["semantic_ready"] or embedding_fingerprint(spec) != model_key:
                continue
            with generation.connect() as previous:
                metrics["history_opened"] += 1
                keys = sorted(missing)
                for offset in range(0, len(keys), 400):
                    batch = keys[offset:offset + 400]
                    for key, blob in previous.execute(
                        f"SELECT content_key,blob FROM chunk_vec_keys WHERE content_key IN ({','.join('?' for _ in batch)})", batch):
                        if not isinstance(blob, bytes) or len(blob) != dimension * 4:
                            metrics["invalid_history_vectors"] += 1
                            continue
                        cache.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?)", (key, dimension, blob))
                        missing.discard(key)
                        metrics["restored"] += 1
                cache.commit()
        except (OSError, ValueError, sqlite3.Error, KeyError):
            continue
    metrics["missing_after"] = len(missing)
    return metrics


def encode_chunks(store, connection, model):
    import numpy as np
    rows = connection.execute("SELECT id,embedding_text FROM chunks ORDER BY id").fetchall()
    model_key = embedding_fingerprint(model.profile)
    keyed = [(number, text, digest(model_key + "\n" + text)) for number, text in rows]
    required = {key for _, _, key in keyed}
    dimension = model.profile["dimension"]
    with closing(sqlite3.connect(store.cache / "vector-cache.sqlite")) as cache:
        cache.execute("CREATE TABLE IF NOT EXISTS vectors(key TEXT PRIMARY KEY,dim INTEGER,blob BLOB)")
        metrics = recover_vectors(store, cache, required, model_key, dimension)
        for offset in range(0, len(keyed), 32):
            batch = keyed[offset:offset + 32]
            keys = sorted({key for _, _, key in batch})
            cached = {}
            for key, dim, blob in cache.execute(
                f"SELECT key,dim,blob FROM vectors WHERE key IN ({','.join('?' for _ in keys)})", keys):
                if dim == dimension and isinstance(blob, bytes) and len(blob) == dimension * 4:
                    cached[key] = blob
            pending = {key: text for _, text, key in batch if key not in cached}
            if pending:
                vectors = model.encode(list(pending.values()))
                if len(vectors) != len(pending):
                    raise ValueError("embedding_batch_count_mismatch")
                for key, vector in zip(pending, vectors, strict=True):
                    array = np.asarray(vector, dtype=np.float32)
                    if array.shape != (dimension,) or not np.isfinite(array).all():
                        raise ValueError("invalid_embedding")
                    blob = array.tobytes()
                    cached[key] = blob
                    cache.execute("INSERT OR REPLACE INTO vectors VALUES(?,?,?)", (key, dimension, blob))
                metrics["encoded"] += len(pending)
            for number, _, key in batch:
                connection.execute("INSERT INTO chunk_vec VALUES(?,?,?)", (number, dimension, cached[key]))
                connection.execute("INSERT OR REPLACE INTO chunk_vec_keys VALUES(?,?)", (key, cached[key]))
            cache.commit()
            if offset % 1024 == 0:
                connection.commit()
                print(f"embedding {min(offset + 32, len(keyed))}/{len(keyed)}", file=sys.stderr, flush=True)
    return metrics


def build(config, *, lexical_only=False, publication=None, job_id=None):
    started = time.perf_counter()
    store = Store(config)
    with file_lock(store.cache / "writer.lock"):
        previous = store.load()
        documents = collect_documents(config)
        if not documents and previous.manifest["source_count"]:
            raise ValueError("empty_transition_requires_review")
        model = None
        if documents and not lexical_only:
            from .runtime import model_imports
            with model_imports():
                model = LocalBackend(config.model_dir, config.cpu_threads)
                model.get_tokenizer()
        chunks, parents = chunk_documents(documents, model)
        if documents and not chunks:
            raise ValueError("sources_produced_no_evidence")
        name, directory = store.create()
        spec = profile()
        metrics = {"required_keys": 0, "encoded": 0, "restored": 0, "history_opened": 0}
        with closing(sqlite3.connect(directory / "vault.sqlite")) as db:
            db.executescript(SCHEMA)
            for document in documents:
                m = document.metadata
                status = m.get("lifecycle", m["status"])
                db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    document.path, document.family, document.kind, document.text, canonical(document.source),
                    canonical(m), status, canonical(m["workspace_ids"]), str(m.get("superseded_by", "")),
                    str(m.get("chip", ""))))
            db.executemany("INSERT INTO sections VALUES(?,?,?,?,?,?)", parents)
            if chunks:
                columns = list(chunks[0])
                db.executemany(f"INSERT INTO chunks({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                    [tuple(c[k] for k in columns) for c in chunks])
            db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
            db.execute("INSERT INTO chunks_fts(chunks_fts,rank) VALUES('integrity-check',1)")
            if model:
                metrics = encode_chunks(store, db, model)
            db.commit()
        manifest = {"schema_version": 2, "kb_id": config.kb_id, "generation": name, "job_id": job_id,
            "created_unix": time.time(), "index_state": "ready" if chunks else "empty",
            "source_count": len(documents), "chunk_count": len(chunks),
            "sources": {d.path: d.source for d in documents}, "model_profile": spec,
            "model_fingerprint": fingerprint(spec), "semantic_ready": bool(model),
            "model_error": "explicit_lexical_only" if lexical_only else "empty_knowledge" if not chunks else "",
            "embedded": len(chunks) if model else 0, "cache_reuse": metrics,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "source_config_sha256": digest(canonical(config.json())),
            "source_coverage": {"empty_pdf_pages": {d.path: d.source["empty_pages"] for d in documents if d.source["empty_pages"]}}}
        atomic_json(directory / "manifest.json", manifest)
        selected = store.load(name)
        if publication:
            publication(store, selected, previous.name)
        else:
            store.publish(selected, expected_previous=previous.name, job_id=job_id)
        return {k: v for k, v in manifest.items() if k != "sources"}
