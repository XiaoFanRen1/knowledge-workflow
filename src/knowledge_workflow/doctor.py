"""Read-only installation diagnostics; readiness is not native invocation proof."""
from pathlib import Path

from .codex_integration import Codex
from .config import KnowledgeConfig
from .distribution import sha256_file, verify_model
from .service import KnowledgeService
from .util import read_json, reject_links


def inspect(root, *, verify_model_hashes=False):
    root = reject_links(Path(root)).resolve()
    state = read_json(root / "installation.json")
    issues = []
    if state.get("state") != "installed":
        issues.append("installation_not_active")
    if (root / "pending-installation.json").exists():
        issues.append("pending_installation_transaction")
    if not Path(state["python"]).is_file() or not Path(state["entry"]).is_file():
        issues.append("runtime_missing")
    if sha256_file(root / "kw.cmd") != state["launcher_sha256"]:
        issues.append("launcher_modified")
    registry = read_json(Path(state["registry"]))
    names = [item["name"] for item in registry["libraries"].values()]
    codex = Codex(Path(state["codex_executable"]), Path(state["codex_home"]))
    if codex.components(names) != state["components"]:
        issues.append("codex_registration_changed")
    libraries = []
    for item in registry["libraries"].values():
        config = KnowledgeConfig.load(item["config"])
        service = KnowledgeService(config)
        try:
            current = service.status()
            if not current["ok"]:
                issues.append("knowledge_status_failed:" + config.kb_id)
            libraries.append({"kb_id": config.kb_id, "name": item["name"], "status": current})
        finally:
            service.close()
    if verify_model_hashes:
        verify_model(Path(state["model_dir"]))
    return {"ok": not issues, "version": state["version"], "commit": state["commit"], "issues": issues,
            "installed_with_codex_version": state["codex_version"], "libraries": libraries,
            "model_hash_check": "passed" if verify_model_hashes else "not_requested",
            "native_client_invocation": "not_run_by_doctor"}
