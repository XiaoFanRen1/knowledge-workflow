"""Immutable schema-2 evidence generations and explicitly owned control data."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager, closing
from dataclasses import dataclass
from pathlib import Path

from .config import KnowledgeConfig
from .models import fingerprint, profile
from .util import atomic_json, canonical, contained, digest, file_lock, read_json

SCHEMA = """
CREATE TABLE documents(path TEXT PRIMARY KEY,family TEXT NOT NULL,kind TEXT NOT NULL,
 text TEXT NOT NULL,source_json TEXT NOT NULL,metadata_json TEXT NOT NULL,status TEXT NOT NULL,
 workspace_json TEXT NOT NULL,superseded_by TEXT NOT NULL,chip TEXT NOT NULL);
CREATE TABLE sections(section_id TEXT PRIMARY KEY,path TEXT NOT NULL,heading TEXT NOT NULL,
 page INTEGER,body TEXT NOT NULL,line_start INTEGER NOT NULL);
CREATE TABLE chunks(id INTEGER PRIMARY KEY,path TEXT NOT NULL,heading TEXT NOT NULL,page TEXT,
 family TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,tokens TEXT NOT NULL,
 section_id TEXT NOT NULL,evidence_id TEXT NOT NULL UNIQUE,start_char INTEGER NOT NULL,
 end_char INTEGER NOT NULL,line_start INTEGER NOT NULL,embedding_text TEXT NOT NULL,role TEXT NOT NULL);
CREATE INDEX chunks_path ON chunks(path);
CREATE INDEX chunks_section ON chunks(section_id);
CREATE VIRTUAL TABLE chunks_fts USING fts5(heading,tokens,path UNINDEXED,body UNINDEXED,
 page UNINDEXED,family UNINDEXED,kind UNINDEXED,content='chunks',content_rowid='id',tokenize='unicode61');
CREATE TABLE chunk_vec(id INTEGER PRIMARY KEY,dim INTEGER NOT NULL,blob BLOB NOT NULL);
CREATE TABLE chunk_vec_keys(content_key TEXT PRIMARY KEY,blob BLOB NOT NULL);
"""
CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures(operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
 record_id TEXT NOT NULL, target_hash TEXT NOT NULL, previous_hash TEXT,
 payload TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS records(record_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL,sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY,operation_id TEXT NOT NULL UNIQUE,
 request_hash TEXT NOT NULL,state TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feedback(event_id TEXT PRIMARY KEY,created REAL NOT NULL,
 query_id TEXT NOT NULL,outcome TEXT NOT NULL,evidence_id TEXT NOT NULL,reason TEXT NOT NULL,detail_json TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Generation:
    name: str
    directory: Path
    manifest: dict

    @property
    def sqlite(self):
        return self.directory / "vault.sqlite"

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.sqlite.as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


class Store:
    def __init__(self, config: KnowledgeConfig):
        self.config = config
        self.cache = config.cache
        self.generations = self.cache / "generations"
        self.current = self.cache / "current.json"

    @contextmanager
    def control(self, *, write=False):
        path = self.cache / "control.sqlite"
        if write:
            self.cache.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=5)
        else:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            if write:
                connection.executescript(CONTROL_SCHEMA)
                connection.execute("BEGIN IMMEDIATE")
            yield connection
            if write:
                connection.commit()
        except BaseException:
            if write:
                connection.rollback()
            raise
        finally:
            connection.close()

    def load(self, name=None):
        if name is None:
            pointer = read_json(self.current)
            if pointer.get("kb_id") != self.config.kb_id:
                raise ValueError("knowledge_identity_mismatch")
            name = pointer.get("generation")
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,96}", name):
            raise ValueError("invalid_generation")
        directory = contained(self.generations, name)
        manifest = read_json(directory / "manifest.json")
        if manifest.get("schema_version") != 2 or manifest.get("generation") != name:
            raise ValueError("unsupported_generation_schema")
        if manifest.get("kb_id") != self.config.kb_id:
            raise ValueError("knowledge_identity_mismatch")
        if not (directory / "vault.sqlite").is_file():
            raise FileNotFoundError("generation_database_missing")
        return Generation(name, directory, manifest)

    def create(self):
        name = time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:12]
        directory = contained(self.generations, name)
        directory.mkdir(parents=True)
        return name, directory

    def validate(self, generation):
        manifest = generation.manifest
        with generation.connect() as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("index_integrity_failed")
            count = db.execute("SELECT count(*) FROM chunks").fetchone()[0]
            sources = db.execute("SELECT count(*) FROM documents").fetchone()[0]
            if count != manifest["chunk_count"] or sources != manifest["source_count"]:
                raise ValueError("index_count_mismatch")
            if count == 0 and (sources != 0 or manifest.get("index_state") != "empty"):
                raise ValueError("empty_index_requires_empty_sources")
            if sources != len(manifest["sources"]):
                raise ValueError("source_manifest_coverage_mismatch")
            if fingerprint(manifest["model_profile"]) != manifest["model_fingerprint"]:
                raise ValueError("index_model_fingerprint_mismatch")
            vector_count = db.execute("SELECT count(*) FROM chunk_vec").fetchone()[0]
            if manifest["semantic_ready"] and vector_count != count:
                raise ValueError("index_vector_coverage_mismatch")
            if db.execute("SELECT count(*) FROM chunk_vec WHERE dim!=? OR length(blob)!=dim*4",
                          (manifest["model_profile"]["dimension"],)).fetchone()[0]:
                raise ValueError("invalid_vectors")
            if db.execute("SELECT count(*) FROM chunks c LEFT JOIN sections s ON c.section_id=s.section_id "
                          "LEFT JOIN documents d ON c.path=d.path WHERE s.section_id IS NULL OR d.path IS NULL").fetchone()[0]:
                raise ValueError("orphan_evidence")
            for row in db.execute("SELECT path,source_json FROM documents"):
                if json.loads(row["source_json"]) != manifest["sources"].get(row["path"]):
                    raise ValueError("source_manifest_mismatch")

    def publish(self, generation, *, expected_previous, job_id=None):
        self.validate(generation)
        previous = read_json(self.current)["generation"] if self.current.exists() else None
        if previous != expected_previous:
            raise ValueError("publication_previous_changed")
        atomic_json(self.current, {"schema_version": 2, "kb_id": self.config.kb_id,
            "generation": generation.name, "previous": previous, "job_id": job_id})

    def initialize_empty(self):
        with file_lock(self.cache / "writer.lock"):
            if self.current.exists():
                raise FileExistsError("index_already_initialized")
            name, directory = self.create()
            with closing(sqlite3.connect(directory / "vault.sqlite")) as db:
                db.executescript(SCHEMA)
                db.commit()
            spec = profile()
            manifest = {"schema_version": 2, "kb_id": self.config.kb_id, "generation": name,
                "index_state": "empty", "source_count": 0, "chunk_count": 0, "sources": {},
                "model_profile": spec, "model_fingerprint": fingerprint(spec), "semantic_ready": False,
                "model_error": "empty_knowledge", "embedded": 0, "created_unix": time.time()}
            atomic_json(directory / "manifest.json", manifest)
            self.publish(self.load(name), expected_previous=None)
            with self.control(write=True):
                pass
        return self.load(name)
