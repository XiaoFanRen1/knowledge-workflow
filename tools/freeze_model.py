"""Compare public pinned model metadata with an explicit local model snapshot."""
import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import HfApi

parser = argparse.ArgumentParser()
parser.add_argument("--model-dir", required=True, type=Path)
args = parser.parse_args()
repo = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
revision = "e8f8c211226b894fcb81acc59f3b34ba3efd5f42"
info = HfApi().model_info(repo, revision=revision, files_metadata=True, token=False)
records = []
for item in info.siblings:
    if item.rfilename in {"config.json", "modules.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json",
                          "sentence_bert_config.json", "config_sentence_transformers.json", "1_Pooling/config.json",
                          "model.safetensors", "pytorch_model.bin", "sentencepiece.bpe.model"}:
        local = args.model_dir / item.rfilename
        raw = local.read_bytes() if local.is_file() else None
        sha = hashlib.sha256(raw).hexdigest() if raw is not None else None
        lfs_sha = item.lfs.sha256 if item.lfs else None
        blob_sha = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() if raw is not None else None
        records.append({"file": item.rfilename, "size": item.size, "sha256": sha,
                        "matches_public_object": sha == lfs_sha if lfs_sha else blob_sha == item.blob_id,
                        "lfs_sha256": lfs_sha})
print(json.dumps({"revision": info.sha, "files": records}, indent=2))
