"""Model process bound to exactly one immutable knowledge configuration."""
from pathlib import Path
from .models import LocalBackend, fingerprint, embedding_fingerprint
from .processes import command
from .storage import Store
from .worker_client import ModelWorkerClient
from .util import canonical, digest


class ModelWorker(ModelWorkerClient):
    def __init__(self, config, **budgets):
        super().__init__(entry=Path(__file__), cache=config.cache,
            command=command("worker", "--config", config.config_path,
                            "--binding-hash", digest(canonical(config.json()))), **budgets)


def serve(config):
    from .worker_protocol import serve as protocol
    from .retrieval import lexical, semantic, merge
    store = Store(config)
    model = LocalBackend(config.model_dir, config.cpu_threads)

    def generation(payload):
        selected = store.load(payload["generation"])
        if selected.manifest["model_fingerprint"] != payload["fingerprint"]:
            raise ValueError("model_fingerprint_mismatch")
        if embedding_fingerprint(model.profile) != embedding_fingerprint(selected.manifest["model_profile"]):
            raise ValueError("unsupported_model_profile")
        return selected

    def prepare(payload):
        import jieba
        jieba.initialize()
        generation(payload)
        model.load()
        return {"shared_validation_tokenizer": True}

    def execute(payload):
        selected = generation(payload)
        semantic_hits = semantic(selected, payload["query"], payload["scope"], model)
        lexical_hits = lexical(selected, payload["query"], payload["scope"])
        return {"hits": merge(lexical_hits, semantic_hits), "semantic_status": "used", "semantic_error": ""}

    protocol(prepare, execute)
