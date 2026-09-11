"""Explicit, immutable binding. Importing this module performs no I/O."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from .util import atomic_json, contained, read_json, reject_links

PROFILE = {
    "backend": "sentence-transformers",
    "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "revision": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
    "dimension": 384, "max_tokens": 128,
    "query_prefix": "", "passage_prefix": "", "reranker": None,
}


@dataclass(frozen=True)
class SourceRoot:
    source_id: str
    path: Path
    suffixes: tuple[str, ...] = (".md", ".txt", ".pdf")


@dataclass(frozen=True)
class KnowledgeConfig:
    config_path: Path
    kb_id: str
    root: Path
    cache: Path
    model_dir: Path
    sources: tuple[SourceRoot, ...]
    cpu_threads: int = 4

    @property
    def notes(self):
        return contained(self.root, "notes")

    def source_path(self, relative: str) -> Path:
        source_id, separator, name = relative.partition("/")
        if not separator:
            raise ValueError("unregistered_source")
        for source in self.sources:
            if source.source_id == source_id:
                return contained(source.path, name)
        raise ValueError("unregistered_source")

    def json(self):
        return {"schema_version": 1, "kb_id": self.kb_id, "root": str(self.root),
                "cache": str(self.cache), "model_dir": str(self.model_dir),
                "cpu_threads": self.cpu_threads,
                "sources": [{"id": s.source_id, "path": str(s.path),
                             "suffixes": list(s.suffixes)} for s in self.sources]}

    @classmethod
    def load(cls, path: str | Path):
        path = reject_links(Path(path)).resolve()
        raw = read_json(path)
        if raw.get("schema_version") != 1 or not re.fullmatch(r"[a-f0-9]{32}", raw.get("kb_id", "")):
            raise ValueError("unsupported_knowledge_config")
        def absolute(key):
            value = Path(raw[key])
            if not value.is_absolute():
                raise ValueError("config_requires_absolute_paths")
            return reject_links(value).resolve()
        root, cache, model = absolute("root"), absolute("cache"), absolute("model_dir")
        if cache == root or cache.is_relative_to(root / "notes") or root.is_relative_to(cache):
            raise ValueError("overlapping_data_paths")
        sources = []
        seen = set()
        for item in raw["sources"]:
            sid = item["id"]
            p = Path(item["path"])
            suffixes = tuple(item.get("suffixes", [".md", ".txt", ".pdf"]))
            if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", sid) or sid in seen or not p.is_absolute():
                raise ValueError("invalid_source_root")
            p = reject_links(p).resolve()
            if p == Path(p.anchor) or p == Path.home().resolve():
                raise ValueError("drive_or_home_directory_is_not_a_source")
            if p == cache or p.is_relative_to(cache) or p == model or p.is_relative_to(model):
                raise ValueError("runtime_directory_is_not_a_source")
            if not suffixes or any(not re.fullmatch(r"\.[a-z0-9]{1,12}", s) for s in suffixes):
                raise ValueError("invalid_source_suffixes")
            seen.add(sid)
            sources.append(SourceRoot(sid, p, suffixes))
        if not any(s.source_id == "notes" and s.path == root / "notes" for s in sources):
            raise ValueError("managed_notes_source_required")
        threads = raw.get("cpu_threads", 4)
        if type(threads) is not int or not 1 <= threads <= 32:
            raise ValueError("invalid_cpu_threads")
        return cls(path, raw["kb_id"], root, cache, model, tuple(sources), threads)


def initialize(root: Path, model_dir: Path, source_roots=()) -> KnowledgeConfig:
    root, model_dir = reject_links(root).resolve(), reject_links(model_dir).resolve()
    if root.exists() and any(root.iterdir()):
        raise FileExistsError("new_knowledge_root_must_be_empty")
    if root == model_dir or root.is_relative_to(model_dir) or model_dir.is_relative_to(root):
        raise ValueError("model_directory_must_be_separate")
    root.mkdir(parents=True, exist_ok=True)
    notes = root / "notes"
    notes.mkdir()
    config = KnowledgeConfig(root / "knowledge.json", uuid.uuid4().hex, root, root / ".index", model_dir,
        (SourceRoot("notes", notes), *tuple(SourceRoot(f"source-{i+1}", reject_links(Path(p)).resolve())
                                           for i, p in enumerate(source_roots))))
    atomic_json(config.config_path, config.json())
    return KnowledgeConfig.load(config.config_path)
