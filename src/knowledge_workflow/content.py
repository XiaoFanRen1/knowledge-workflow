"""Registered sources, faithful text snapshots, token-bounded original spans."""
from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .util import digest, reject_links


@dataclass
class Document:
    path: str
    family: str
    kind: str
    text: str
    metadata: dict
    source: dict
    pages: list | None = None


def tokenize(text: str) -> str:
    import jieba
    return " ".join(word.lower() for word in jieba.lcut(text) if word.strip())


def frontmatter(text):
    import yaml
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, 0
    end = next((i for i in range(1, min(120, len(lines))) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("unterminated_frontmatter")
    value = yaml.safe_load("\n".join(lines[1:end])) or {}
    if not isinstance(value, dict):
        raise ValueError("frontmatter_requires_mapping")
    return json.loads(json.dumps(value, default=str)), end + 1


def inventory(config):
    found = []
    seen = set()
    excluded = (config.cache.resolve(), config.model_dir.resolve())
    for source in config.sources:
        reject_links(source.path)
        if not source.path.is_dir():
            raise FileNotFoundError("registered_source_unavailable: " + source.source_id)
        for directory, directories, files in os.walk(source.path, followlinks=False):
            current = Path(directory)
            kept = []
            for name in sorted(directories):
                path = current / name
                if name.startswith(".") or name in {"node_modules", "__pycache__"}:
                    continue
                reject_links(path)
                if any(path.resolve() == p or path.resolve().is_relative_to(p) for p in excluded):
                    continue
                kept.append(name)
            directories[:] = kept
            for name in sorted(files):
                path = current / name
                if name.startswith(".") or path.suffix.lower() not in source.suffixes:
                    continue
                reject_links(path)
                resolved = path.resolve()
                if resolved in seen:
                    raise ValueError("overlapping_registered_sources")
                seen.add(resolved)
                found.append((source.source_id + "/" + path.relative_to(source.path).as_posix(), path))
                if len(found) > 10000:
                    raise ValueError("source_inventory_budget_exceeded")
    return sorted(found)


def collect_documents(config):
    documents = []
    for relative, path in inventory(config):
        before = path.stat()
        if before.st_size > 64 * 1024 * 1024:
            raise ValueError("source_size_budget_exceeded: " + relative)
        raw = path.read_bytes()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError("source_changed_during_read: " + relative)
        pages = None
        metadata = {}
        if path.suffix.lower() == ".pdf":
            from pypdf import PdfReader, __version__
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                raise ValueError("encrypted_pdf_not_supported: " + relative)
            pages = [(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
            text = "\n".join(body for _, body in pages)
            extractor, kind = "pypdf-" + __version__, "pdf"
            if not text.strip():
                raise ValueError("pdf_requires_ocr: " + relative)
        else:
            text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            kind = "md" if path.suffix.lower() == ".md" else "text"
            metadata, _ = frontmatter(text) if kind == "md" else ({}, 0)
            extractor = "utf8-lf-v1"
        if not text.strip():
            raise ValueError("source_has_no_text: " + relative)
        metadata.setdefault("status", "active")
        metadata.setdefault("maturity", "unspecified")
        metadata.setdefault("role", "knowledge")
        metadata.setdefault("workspace_ids", [])
        if isinstance(metadata["workspace_ids"], str):
            metadata["workspace_ids"] = [metadata["workspace_ids"]]
        source = {"sha256": digest(raw), "text_sha256": digest(text), "kind": kind,
            "bytes": len(raw), "mtime_ns": after.st_mtime_ns, "extractor": extractor,
            "empty_pages": [page for page, body in pages if not body.strip()] if pages else [],
            "hash_scheme": "sha256-raw+sha256-text-lf-v1"}
        documents.append(Document(relative, "current", kind, text, metadata, source, pages))
    return documents


def sections(document):
    if document.pages is not None:
        for page, body in document.pages:
            yield f"{Path(document.path).name} / p{page}", body, 1, page
        return
    _, skip = frontmatter(document.text) if document.kind == "md" else ({}, 0)
    lines = document.text.splitlines(keepends=True)
    hierarchy = []
    start, heading, fenced = skip, Path(document.path).stem, False
    for i in range(skip, len(lines)):
        line = re.sub(r"^(?:\s*>\s*)+", "", lines[i])
        if re.match(r"^\s*(```|~~~)", line):
            fenced = not fenced
        match = None if fenced else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            if i > start:
                yield heading, "".join(lines[start:i]), start + 1, None
            level, title = len(match.group(1)), match.group(2).strip()
            hierarchy = [(n, h) for n, h in hierarchy if n < level] + [(level, title)]
            heading, start = " / ".join(h for _, h in hierarchy), i
    if start < len(lines):
        yield heading, "".join(lines[start:]), start + 1, None


def bounded_spans(body, context, model):
    start = 0
    while start < len(body):
        if model:
            low, high, end = start + 1, min(len(body), start + model.profile["max_tokens"] * 6), start
            while low <= high:
                middle = (low + high) // 2
                if model.length(context + "\n" + body[start:middle]) <= model.profile["max_tokens"]:
                    end, low = middle, middle + 1
                else:
                    high = middle - 1
            if end == start:
                raise ValueError("context_exhausts_document_budget")
        else:
            end = min(len(body), start + 1000)
        if end < len(body):
            boundary = body.rfind("\n", start + (end - start) // 2, end)
            if boundary >= start:
                end = boundary + 1
        if model:
            while end > start and model.length(context + "\n" + body[start:end]) > model.profile["max_tokens"]:
                end -= 1
            if end == start:
                raise ValueError("context_exhausts_document_budget")
        if body[start:end].strip():
            yield start, end, body[start:end]
        start = end


def chunk_documents(documents, model):
    chunks, parents = [], []
    for document in documents:
        for heading, body, line, page in sections(document):
            if not body.strip():
                continue
            sid = digest(f"{document.path}\n{document.source['text_sha256']}\n{heading}\n{line}\n{page}")
            parents.append((sid, document.path, heading, page, body, line))
            context = heading
            if model:
                tokenizer = model.get_tokenizer()
                context = tokenizer.decode(tokenizer.encode(heading, add_special_tokens=False)[-32:])
            for start, end, part in bounded_spans(body, context, model):
                chunks.append({"path": document.path, "heading": heading, "page": page,
                    "family": document.family, "kind": document.kind, "body": part,
                    "tokens": tokenize(heading + "\n" + part), "section_id": sid,
                    "evidence_id": digest(f"{sid}:{start}:{end}"), "start_char": start, "end_char": end,
                    "line_start": line + body[:start].count("\n"), "embedding_text": context + "\n" + part,
                    "role": document.metadata["role"]})
    return chunks, parents
