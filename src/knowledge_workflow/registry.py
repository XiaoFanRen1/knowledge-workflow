"""Machine-local bindings; project files contain logical references only."""
from pathlib import Path

from .config import KnowledgeConfig
from .util import atomic_json, canonical, digest, file_lock, read_json, reject_links


def register(path: Path, config: KnowledgeConfig, knowledge_ref="project"):
    with file_lock(path.with_suffix(".lock")):
        value = read_json(path) if path.exists() else {"schema_version": 1, "libraries": {}, "bindings": {}, "projects": {}}
        if value.get("schema_version") != 1:
            raise ValueError("unsupported_registry")
        item = {"config": str(config.config_path), "name": "kw-" + config.kb_id}
        if config.kb_id in value["libraries"] and value["libraries"][config.kb_id] != item:
            raise ValueError("library_binding_conflict")
        if knowledge_ref in value["bindings"] and value["bindings"][knowledge_ref] != config.kb_id:
            raise ValueError("logical_binding_conflict")
        value["libraries"][config.kb_id] = item
        value["bindings"][knowledge_ref] = config.kb_id
        atomic_json(path, value)
    return value


def bind_project(path: Path, project: Path, reference: str, kb_id: str):
    with file_lock(path.with_suffix(".lock")):
        value = read_json(path)
        if kb_id not in value["libraries"]:
            raise ValueError("unknown_library")
        project = str(reject_links(project).resolve())
        value.setdefault("projects", {}).setdefault(project, {})[reference] = kb_id
        atomic_json(path, value)
    return {"ok": True, "project": project, "knowledge_ref": reference, "kb_id": kb_id}


def resolve(path, project, reference):
    value = read_json(Path(path))
    project = str(reject_links(Path(project)).resolve())
    key = value.get("projects", {}).get(project, {}).get(reference) or value.get("bindings", {}).get(reference)
    if key not in value["libraries"]:
        raise ValueError("knowledge_binding_not_configured")
    return {"ok": True, "kb_id": key, **value["libraries"][key]}


def start_runners(path):
    from .runner import start
    value = read_json(Path(path))
    results = []
    for identifier, item in value["libraries"].items():
        config = KnowledgeConfig.load(item["config"])
        if config.kb_id != identifier:
            raise ValueError("registry_identity_mismatch")
        results.append(start(config))
    return {"ok": True, "runners": results}
