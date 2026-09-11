"""Pinned local embeddings. Downloads belong to the installer, never queries."""
from __future__ import annotations

import json
from pathlib import Path

from .config import PROFILE
from .util import digest, reject_links


def profile():
    return {**PROFILE, "name": "multilingual", "chunk_policy": "token-sections-v3"}


def fingerprint(spec):
    return digest(json.dumps(spec, sort_keys=True, ensure_ascii=False))


def embedding_fingerprint(spec):
    return fingerprint({k: v for k, v in spec.items() if k not in {"name", "reranker", "chunk_policy"}})


def local_snapshot(model_dir: Path) -> Path:
    direct = reject_links(model_dir).resolve()
    path = direct if (direct / "config.json").is_file() else (
        direct / ("models--" + PROFILE["model"].replace("/", "--")) / "snapshots" / PROFILE["revision"])
    reject_links(path)
    for name in ("config.json", "modules.json", "tokenizer.json", "model.safetensors", "1_Pooling/config.json"):
        if not (path / name).is_file():
            raise FileNotFoundError("model_incomplete: " + name)
    return path


class LocalBackend:
    def __init__(self, model_dir: Path, cpu_threads: int = 4):
        self.model_dir = model_dir
        self.cpu_threads = cpu_threads
        self.profile = profile()
        self.tokenizer = None
        self.model = None
        self.validated_snapshot = None

    def snapshot(self):
        if self.validated_snapshot is None:
            from .distribution import verify_model
            path = local_snapshot(self.model_dir)
            verify_model(path, strict_inventory=False)
            self.validated_snapshot = path
        return self.validated_snapshot

    def get_tokenizer(self):
        if self.tokenizer is None:
            snapshot = self.snapshot()
            from transformers import AutoTokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(str(snapshot),
                local_files_only=True, trust_remote_code=False)
        return self.tokenizer

    def load(self):
        if self.model is None:
            snapshot = self.snapshot()
            import torch
            from sentence_transformers import SentenceTransformer
            torch.set_num_threads(self.cpu_threads)
            self.model = SentenceTransformer(str(snapshot),
                local_files_only=True, trust_remote_code=False, device="cpu", model_kwargs={"use_safetensors": True})
            self.model.max_seq_length = self.profile["max_tokens"]
            self.tokenizer = self.model.tokenizer
        return self.model

    def length(self, text):
        return len(self.get_tokenizer().encode(text, add_special_tokens=True, truncation=False))

    def encode(self, texts: list[str], *, query=False):
        import numpy as np
        inputs = [self.profile["query_prefix" if query else "passage_prefix"] + text for text in texts]
        if any(self.length(text) > self.profile["max_tokens"] for text in inputs):
            raise ValueError("query_too_long" if query else "document_token_budget_exceeded")
        output = self.load().encode(inputs, batch_size=32, normalize_embeddings=True,
                                   show_progress_bar=False, convert_to_numpy=True)
        result = np.asarray(output, dtype=np.float32)
        if result.shape != (len(texts), self.profile["dimension"]) or not np.isfinite(result).all():
            raise ValueError("invalid_embedding")
        return result
