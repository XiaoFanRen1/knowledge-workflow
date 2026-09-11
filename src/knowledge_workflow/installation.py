"""Inactive version preparation, reviewed activation and owned uninstall."""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
import uuid
from pathlib import Path

from .codex_integration import Codex
from .command_runner import run
from .config import KnowledgeConfig, initialize
from .distribution import acquire_model, sha256_file, verify_bundle, verify_model
from .storage import Store
from .util import atomic_bytes, atomic_json, canonical, contained, digest, file_lock, read_json, reject_links


def tree(root):
    result = {}
    for number, path in enumerate(sorted(root.rglob("*"))):
        reject_links(path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256_file(path)
        if number and number % 3000 == 0:
            print(f"installation inventory: {number} entries", file=sys.stderr, flush=True)
    return result


def validate_roots(root, data):
    root, data = reject_links(Path(root)).resolve(), reject_links(Path(data)).resolve()
    if root == data or root.is_relative_to(data) or data.is_relative_to(root):
        raise ValueError("program_and_data_roots_must_be_separate")
    if root == Path(root.anchor) or data == Path(data.anchor):
        raise ValueError("drive_root_is_not_an_installation_target")
    return root, data


def start_selected_runners(prepared, registry_path):
    return run([prepared["python"], "-I", "-B", "-X", "utf8", prepared["entry"],
                "runner-registry", "--registry", registry_path], phase="Start selected maintenance runtime", timeout=30)


def preview(bundle, root, data, codex, codex_home, *, startup=True):
    manifest = verify_bundle(bundle)
    root, data = validate_roots(root, data)
    state = read_json(root / "installation.json") if (root / "installation.json").exists() else None
    if root.exists() and not state and any(root.iterdir()):
        raise ValueError("installation_target_is_not_empty_or_owned")
    if state and state["data"] != str(data):
        raise ValueError("changing_the_data_root_requires_explicit_migration")
    return {"ok": True, "mode": "preview", "release": manifest["version"], "commit": manifest["commit"],
        "program_root": str(root), "data_root": str(data), "codex_home": str(codex_home),
        "codex_executable": str(codex), "previous_version": state.get("version") if state else None,
        "actions": ["prepare an isolated runtime", "verify model files", "test synthetic semantic retrieval",
                    "register the owned local plugin and MCP", "start the independent local maintenance runner"],
        "user_logon_startup": startup, "data_retained_on_uninstall": True,
        "model_settings_changed": False, "global_agents_replaced": False}


def prepare(bundle, root, data, *, model_source=None, offline=False):
    """Create the venv at its FINAL inactive path: virtual environments are not relocatable."""
    bundle = Path(bundle).resolve()
    manifest = verify_bundle(bundle)
    root, data = validate_roots(root, data)
    version = manifest["version"]
    if not all(c.isalnum() or c in ".-" for c in version):
        raise ValueError("invalid_release_version")
    version_root = contained(root, "versions/" + version)
    marker = version_root / "preparation.json"
    if version_root.exists():
        if not marker.exists() or read_json(marker)["commit"] != manifest["commit"]:
            raise ValueError("release_version_collision")
        record = read_json(marker)
        if record.get("state") != "prepared":
            raise ValueError("unfinished_runtime_preparation_requires_recovery")
        if tree(version_root / "venv") != record.get("runtime_files"):
            raise ValueError("prepared_runtime_modified")
        verify_model(Path(record["model_dir"]))
        return record
    else:
        version_root.mkdir(parents=True)
        atomic_json(marker, {"commit": manifest["commit"], "state": "preparing"})
    python = version_root / "venv/Scripts/python.exe"
    if not python.exists():
        run([sys.executable, "-I", "-m", "venv", str(version_root / "venv")], phase="Create isolated runtime", timeout=120)
    run([python, "-I", "-m", "pip", "install", "--isolated", "--no-index", "--disable-pip-version-check",
         "--find-links", bundle / "wheels", "--require-hashes", "-r", bundle / "requirements/windows-cp314.lock.txt"],
        phase="Install verified dependencies", timeout=900)
    run([python, "-I", "-m", "pip", "install", "--isolated", "--no-index", "--no-deps",
         "--disable-pip-version-check", bundle / manifest["runtime_wheel"]], phase="Install workflow runtime", timeout=120)
    run([python, "-I", "-m", "pip", "check"], phase="Verify dependency consistency", timeout=60)
    model = data / "models" / manifest["model_revision"]
    acquire_model(model, existing=model_source, offline=offline)
    entry = version_root / "venv/Lib/site-packages/knowledge_workflow/entry.py"
    if not entry.is_file():
        raise ValueError("installed_entry_missing")
    run([python, "-I", "-B", "-X", "utf8", entry, "self-test", "--model-dir", model],
        phase="Verify isolated semantic knowledge lifecycle", timeout=240)
    record = {"state": "prepared", "version": version, "commit": manifest["commit"],
              "python": str(python), "entry": str(entry), "model_dir": str(model),
              "runtime_files": tree(version_root / "venv")}
    atomic_json(marker, record)
    return record


def activate(bundle, root, data, codex_executable, codex_home, prepared, *, startup=True, startup_target=None):
    from . import registry, runner
    from . import startup as startup_module
    bundle = Path(bundle).resolve()
    root, data = validate_roots(root, data)
    manifest = verify_bundle(bundle)
    codex = Codex(Path(codex_executable), Path(codex_home))
    expected_version = contained(root, "versions/" + manifest["version"])
    if (prepared.get("commit") != manifest["commit"] or prepared.get("version") != manifest["version"]
            or Path(prepared["python"]).resolve() != expected_version / "venv/Scripts/python.exe"
            or Path(prepared["entry"]).resolve() != expected_version / "venv/Lib/site-packages/knowledge_workflow/entry.py"):
        raise ValueError("prepared_runtime_identity_mismatch")
    codex_version = codex.capabilities()
    with file_lock(root / "installation.lock"):
        state_path = root / "installation.json"
        old = read_json(state_path) if state_path.exists() else None
        pending = root / "pending-installation.json"
        if pending.exists():
            raise ValueError("pending_installation_requires_recovery")
        if old and tree(root / "marketplace") != old["marketplace_files"]:
            raise ValueError("installed_plugin_source_modified")
        if old and (old["data"] != str(data) or old["codex_home"] != str(codex.home)):
            raise ValueError("installation_binding_changed")
        existing = codex.components([])
        if not old and (existing["marketplace"] is not None or existing["plugin"] is not None):
            raise ValueError("codex_components_modified_or_unowned")
        config_path = data / "library/knowledge.json"
        if config_path.exists():
            config = KnowledgeConfig.load(config_path)
        else:
            config = initialize(data / "library", Path(prepared["model_dir"]))
            Store(config).initialize_empty()
        registry_path = data / "registry.json"
        registry_value = registry.register(registry_path, config)
        libraries = list(registry_value["libraries"].values())
        for item in libraries:
            library = KnowledgeConfig.load(item["config"])
            if library.root.is_relative_to(root) or library.cache.is_relative_to(root):
                raise ValueError("knowledge_data_cannot_live_inside_program_root")
        names = [item["name"] for item in libraries]
        before = codex.components(names)
        expected = old["components"] if old else {"marketplace": None, "plugin": None, "mcp": {name: None for name in names}}
        if before != expected:
            raise ValueError("codex_components_modified_or_unowned")
        unowned_before = codex.unowned_hash(names)
        for item in libraries:
            stopped = runner.stop(KnowledgeConfig.load(item["config"]))
            if not stopped["ok"]:
                raise RuntimeError("active_maintenance_prevents_activation")
        tx = contained(root, "transactions/" + uuid.uuid4().hex)
        tx.mkdir(parents=True)
        receipt = {"schema_version": 1, "transaction": str(tx), "old": old, "prepared": prepared,
                   "before_components": before, "last_components": before, "phase": "prepared",
                   "codex_executable": str(codex.executable), "codex_home": str(codex.home),
                   "libraries": libraries, "unowned_hash": unowned_before, "registry": str(registry_path)}
        if old and old.get("startup"):
            old_shortcut = Path(old["startup"]["path"])
            if old_shortcut.exists():
                if sha256_file(old_shortcut) != old["startup"]["sha256"]:
                    raise ValueError("startup_shortcut_modified")
                atomic_bytes(tx / "startup-old.lnk", old_shortcut.read_bytes())
        if (root / "kw.cmd").exists():
            if not old or sha256_file(root / "kw.cmd") != old["launcher_sha256"]:
                raise ValueError("installed_launcher_modified_or_unowned")
            atomic_bytes(tx / "launcher-old.cmd", (root / "kw.cmd").read_bytes())
        atomic_json(pending, receipt)
        stage = tx / "marketplace-new"
        shutil.copytree(bundle / "marketplace", stage)
        installed_marketplace = root / "marketplace"
        try:
            if installed_marketplace.exists():
                os.replace(installed_marketplace, tx / "marketplace-old")
            os.replace(stage, installed_marketplace)
            receipt["phase"] = "registering"
            atomic_json(pending, receipt)
            codex.register(installed_marketplace, prepared["python"], prepared["entry"], libraries)
            receipt["last_components"] = codex.components(names)
            receipt["phase"] = "registered"
            atomic_json(pending, receipt)
            if codex.unowned_hash(names) != unowned_before:
                raise RuntimeError("unowned_codex_configuration_changed")
            shortcut = None
            if startup:
                shortcut = startup_module.install(prepared["python"], prepared["entry"], registry_path,
                    target=startup_target, expected_hash=old.get("startup", {}).get("sha256") if old and old.get("startup") else None)
            elif old and old.get("startup"):
                startup_module.remove(old["startup"])
            receipt["startup"] = shortcut
            atomic_json(pending, receipt)
            start_selected_runners(prepared, registry_path)
            relative_python = Path(prepared["python"]).relative_to(root)
            relative_entry = Path(prepared["entry"]).relative_to(root)
            launcher = (f'@echo off\r\n"%~dp0{relative_python}" -I -B -X utf8 '
                        f'"%~dp0{relative_entry}" %*\r\n').encode("ascii")
            if old and (root / "kw.cmd").exists() and sha256_file(root / "kw.cmd") != old["launcher_sha256"]:
                raise ValueError("installed_launcher_modified")
            atomic_bytes(root / "kw.cmd", launcher)
            state = {"schema_version": 1, **prepared, "state": "installed", "root": str(root), "data": str(data),
                "codex_executable": str(codex.executable), "codex_home": str(codex.home), "codex_version": codex_version,
                "registry": str(registry_path), "components": codex.components(names), "startup": shortcut,
                "marketplace_files": tree(installed_marketplace), "launcher_sha256": digest(launcher),
                "previous": {**old, "previous": None} if old else None, "bundle_sha256": sha256_file(bundle / "release.json"),
                "rollback_material": str(tx)}
            atomic_json(state_path, state)
            receipt["phase"] = "installed"
            receipt["installed_state_sha256"] = sha256_file(state_path)
            receipt["owned_files"] = tree(tx)
            atomic_json(tx / "receipt.json", receipt)
            pending.unlink()
            return {"ok": True, "version": state["version"], "config": str(config.config_path),
                    "command": str(root / "kw.cmd"), "native_client_acceptance": "pending",
                    "data_retained_on_uninstall": True}
        except BaseException:
            # Do not overwrite concurrent Codex edits or erase a possibly referenced runtime.
            receipt["observed_components"] = codex.components(names)
            receipt["observed_marketplace_files"] = tree(installed_marketplace) if installed_marketplace.exists() else {}
            receipt["phase"] = "recovery_required"
            atomic_json(pending, receipt)
            raise


def recover_pending(root):
    """Restore reviewed own components only; refuse intervening user changes."""
    from . import registry, runner, startup
    root = reject_links(Path(root)).resolve()
    pending = root / "pending-installation.json"
    with file_lock(root / "installation.lock"):
        receipt = read_json(pending)
        tx = reject_links(Path(receipt["transaction"])).resolve()
        if not tx.is_relative_to(root / "transactions"):
            raise ValueError("untrusted_installation_transaction")
        codex = Codex(Path(receipt["codex_executable"]), Path(receipt["codex_home"]))
        names = [item["name"] for item in receipt["libraries"]]
        actual = codex.components(names)
        expected = receipt.get("observed_components", actual)
        if actual != expected:
            raise ValueError("codex_changed_after_failed_installation")
        marketplace = root / "marketplace"
        observed = receipt.get("observed_marketplace_files")
        if observed is not None and tree(marketplace) != observed:
            raise ValueError("plugin_source_changed_after_failed_installation")
        # The only permitted observed values are a previous owned component or this candidate.
        for name, component in expected["mcp"].items():
            previous = receipt["before_components"]["mcp"].get(name)
            item = next(item for item in receipt["libraries"] if item["name"] == name)
            proposed = {"command": receipt["prepared"]["python"], "args": ["-I", "-B", "-X", "utf8",
                receipt["prepared"]["entry"], "mcp", "--config", item["config"]]}
            if component is not None and component != previous and component != proposed:
                raise ValueError("unowned_mcp_component_in_transaction")
        component = expected["marketplace"]
        if component is not None and component != receipt["before_components"]["marketplace"]:
            candidate_source = str(component.get("source", "")).removeprefix("\\\\?\\")
            if component.get("source_type") != "local" or Path(candidate_source).resolve() != marketplace:
                raise ValueError("unowned_marketplace_in_transaction")
        if expected["plugin"] not in (None, receipt["before_components"]["plugin"], {"enabled": True}):
            raise ValueError("unowned_plugin_configuration_in_transaction")
        for item in receipt["libraries"]:
            if not runner.stop(KnowledgeConfig.load(item["config"]))["ok"]:
                raise RuntimeError("active_maintenance_prevents_recovery")
        codex.remove(names)
        if receipt.get("startup"):
            startup.remove(receipt["startup"])
        if marketplace.exists():
            os.replace(marketplace, tx / "marketplace-failed")
        old = receipt["old"]
        if old:
            if not (tx / "marketplace-old").is_dir():
                raise ValueError("previous_plugin_source_missing")
            os.replace(tx / "marketplace-old", marketplace)
            codex.register(marketplace, old["python"], old["entry"], receipt["libraries"])
            if codex.components(names) != receipt["before_components"]:
                raise RuntimeError("codex_rollback_verification_failed")
            if old.get("startup"):
                target = reject_links(Path(old["startup"]["path"])).resolve()
                if target.exists():
                    raise ValueError("startup_target_changed_during_recovery")
                atomic_bytes(target, (tx / "startup-old.lnk").read_bytes())
            atomic_bytes(root / "kw.cmd", (tx / "launcher-old.cmd").read_bytes())
            atomic_json(root / "installation.json", old)
            start_selected_runners(old, receipt["registry"])
        if codex.unowned_hash(names) != receipt["unowned_hash"]:
            raise RuntimeError("unowned_configuration_requires_review")
        receipt["phase"] = "recovered"
        receipt["owned_files"] = {k: v for k, v in tree(tx).items() if k != "receipt.json"}
        atomic_json(tx / "receipt.json", receipt)
        pending.unlink()
    return {"ok": True, "state": "recovered", "data_retained": True,
            "previous_version": old["version"] if old else None}


def deactivate(root):
    """Unregister first; the external bootstrap removes files after this process exits."""
    from . import runner, startup
    root = reject_links(Path(root)).resolve()
    with file_lock(root / "installation.lock"):
        state = read_json(root / "installation.json")
        if (root / "pending-installation.json").exists():
            raise ValueError("pending_installation_requires_recovery")
        codex = Codex(Path(state["codex_executable"]), Path(state["codex_home"]))
        registry = read_json(Path(state["registry"]))
        names = [item["name"] for item in registry["libraries"].values()]
        if state.get("state") != "unregistered" and codex.components(names) != state["components"]:
            raise ValueError("codex_components_modified")
        if tree(root / "marketplace") != state["marketplace_files"] or sha256_file(root / "kw.cmd") != state["launcher_sha256"]:
            raise ValueError("installed_files_modified")
        for item in registry["libraries"].values():
            if not runner.stop(KnowledgeConfig.load(item["config"]))["ok"]:
                raise RuntimeError("active_maintenance_prevents_uninstall")
        if state.get("state") != "unregistered":
            codex.remove(names)
            if state.get("startup"):
                startup.remove(state["startup"])
        state["state"] = "unregistered"
        atomic_json(root / "installation.json", state)
        return {"ok": True, "state": "unregistered", "data_retained": state["data"],
                "program_cleanup": "external_bootstrap_after_process_exit"}


def rollback(root):
    """Switch to the retained verified runtime and plugin; do not migrate knowledge data."""
    from . import registry, runner, startup
    root = reject_links(Path(root)).resolve()
    with file_lock(root / "installation.lock"):
        state = read_json(root / "installation.json")
        old = state.get("previous")
        if not old or (root / "pending-installation.json").exists():
            raise ValueError("no_completed_rollback_candidate")
        material = reject_links(Path(state["rollback_material"])).resolve()
        if not material.is_relative_to(root / "transactions"):
            raise ValueError("untrusted_rollback_material")
        if tree(material / "marketplace-old") != old["marketplace_files"]:
            raise ValueError("rollback_plugin_source_modified")
        marker = read_json(Path(old["python"]).parents[2] / "preparation.json")
        if tree(Path(old["python"]).parents[1]) != marker["runtime_files"]:
            raise ValueError("rollback_runtime_modified")
        codex = Codex(Path(state["codex_executable"]), Path(state["codex_home"]))
        libraries = list(read_json(Path(state["registry"]))["libraries"].values())
        names = [item["name"] for item in libraries]
        if codex.components(names) != state["components"] or tree(root / "marketplace") != state["marketplace_files"]:
            raise ValueError("installed_components_modified")
        for item in libraries:
            # An incompatible old reader cannot be made compatible by changing metadata.
            run([old["python"], "-I", "-B", "-X", "utf8", old["entry"], "status", "--config", item["config"]],
                phase="Check rollback data compatibility", timeout=30)
            if not runner.stop(KnowledgeConfig.load(item["config"]))["ok"]:
                raise RuntimeError("active_maintenance_prevents_rollback")
        tx = contained(root, "transactions/" + uuid.uuid4().hex)
        tx.mkdir(parents=True)
        atomic_bytes(tx / "launcher-old.cmd", (root / "kw.cmd").read_bytes())
        if state.get("startup"):
            current_shortcut = Path(state["startup"]["path"])
            if sha256_file(current_shortcut) != state["startup"]["sha256"]:
                raise ValueError("startup_shortcut_modified")
            atomic_bytes(tx / "startup-old.lnk", current_shortcut.read_bytes())
        receipt = {"schema_version": 1, "transaction": str(tx), "old": state, "prepared": old,
            "before_components": state["components"], "last_components": state["components"],
            "phase": "rollback", "codex_executable": state["codex_executable"], "codex_home": state["codex_home"],
            "libraries": libraries, "registry": state["registry"], "unowned_hash": codex.unowned_hash(names)}
        pending = root / "pending-installation.json"
        atomic_json(pending, receipt)
        try:
            shutil.copytree(material / "marketplace-old", tx / "marketplace-new")
            os.replace(root / "marketplace", tx / "marketplace-old")
            os.replace(tx / "marketplace-new", root / "marketplace")
            codex.register(root / "marketplace", old["python"], old["entry"], libraries)
            receipt["last_components"] = codex.components(names)
            shortcut = None
            if old.get("startup"):
                shortcut = startup.install(old["python"], old["entry"], state["registry"],
                    target=Path(old["startup"]["path"]), expected_hash=state.get("startup", {}).get("sha256") if state.get("startup") else None)
            elif state.get("startup"):
                startup.remove(state["startup"])
            receipt["startup"] = shortcut
            atomic_bytes(root / "kw.cmd", (material / "launcher-old.cmd").read_bytes())
            restored = {**old, "components": codex.components(names), "startup": shortcut,
                        "previous": {**state, "previous": None}, "rollback_material": str(tx)}
            if codex.unowned_hash(names) != receipt["unowned_hash"]:
                raise RuntimeError("unowned_codex_configuration_changed")
            start_selected_runners(old, state["registry"])
            atomic_json(root / "installation.json", restored)
            receipt["phase"] = "rolled_back"
            receipt["owned_files"] = tree(tx)
            atomic_json(tx / "receipt.json", receipt)
            pending.unlink()
            return {"ok": True, "state": "rolled_back", "version": restored["version"], "data_migrated": False}
        except BaseException:
            receipt.update(phase="recovery_required", observed_components=codex.components(names),
                           observed_marketplace_files=tree(root / "marketplace"))
            atomic_json(pending, receipt)
            raise


def register_library(root, config_path, knowledge_ref):
    from . import registry
    root = reject_links(Path(root)).resolve()
    config = KnowledgeConfig.load(config_path)
    if config.root.is_relative_to(root) or config.cache.is_relative_to(root):
        raise ValueError("knowledge_data_cannot_live_inside_program_root")
    Store(config).load()
    with file_lock(root / "installation.lock"):
        state = read_json(root / "installation.json")
        if state.get("state") != "installed" or (root / "pending-installation.json").exists():
            raise ValueError("installation_not_ready")
        registry_path = Path(state["registry"])
        current = read_json(registry_path)
        if knowledge_ref in current["bindings"] and current["bindings"][knowledge_ref] != config.kb_id:
            raise ValueError("logical_binding_conflict")
        codex = Codex(Path(state["codex_executable"]), Path(state["codex_home"]))
        names = [item["name"] for item in current["libraries"].values()]
        if codex.components(names) != state["components"]:
            raise ValueError("codex_components_modified")
        name = "kw-" + config.kb_id
        if config.kb_id in current["libraries"]:
            if current["libraries"][config.kb_id]["config"] != str(config.config_path):
                raise ValueError("library_binding_conflict")
        elif codex.components([name])["mcp"][name] is not None:
            raise ValueError("mcp_name_collision")
        else:
            proposed = {"command": state["python"], "args": ["-I", "-B", "-X", "utf8", state["entry"], "mcp", "--config", str(config.config_path)]}
            try:
                codex.call("mcp", "add", name, "--", proposed["command"], *proposed["args"])
                registry.register(registry_path, config, knowledge_ref)
            except BaseException:
                if codex.components([name])["mcp"][name] == proposed:
                    codex.call("mcp", "remove", name)
                raise
            names.append(name)
        registry.register(registry_path, config, knowledge_ref)
        state["components"] = codex.components(names)
        atomic_json(root / "installation.json", state)
        start_selected_runners(state, registry_path)
        return {"ok": True, "kb_id": config.kb_id, "mcp_name": name, "knowledge_ref": knowledge_ref}
