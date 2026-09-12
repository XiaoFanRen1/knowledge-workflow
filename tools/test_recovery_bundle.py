"""Prove a new frozen bundle repairs a legacy transaction without running old entry code."""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import psutil

source = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(source / "src"))
from knowledge_workflow.config import initialize
from knowledge_workflow.codex_integration import Codex
from knowledge_workflow.distribution import verify_bundle
from knowledge_workflow.installation import tree
from knowledge_workflow.storage import Store
from knowledge_workflow.util import atomic_json

parser = argparse.ArgumentParser()
parser.add_argument("--bundle", required=True, type=Path)
parser.add_argument("--codex", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
bundle = args.bundle.resolve()
release = verify_bundle(bundle)
with tempfile.TemporaryDirectory(prefix="kw-frozen-recovery-") as temporary:
    root = Path(temporary).resolve()
    program, data, home = [root / name for name in ("program", "data", "codex")]
    home.mkdir()
    (home / "config.toml").write_text('model = "synthetic-original"\n\n[mcp_servers.existing]\ncommand = "python.exe"\nargs = []\nstartup_timeout_sec = 17\ntool_timeout_sec = 61\n', encoding="utf-8")
    cli = Codex(args.codex, home)
    legacy_hash = cli.unowned_hash([], version=1)
    version = program / "versions/0.0.0-legacy"
    subprocess.run([sys.executable, "-I", "-m", "venv", "--without-pip", str(version / "venv")], check=True)
    site = version / "venv/Lib/site-packages"
    shutil.copytree(Path(psutil.__file__).parent, site / "psutil")
    old_package = site / "knowledge_workflow"
    old_package.mkdir()
    for name in ("__init__.py", "entry.py"):
        (old_package / name).write_text('raise RuntimeError("OLD_RECOVERY_CODE_MUST_NOT_RUN")\n', encoding="utf-8")
    prepared = {"state": "prepared", "commit": "a" * 40, "version": "0.0.0-legacy",
        "python": str(version / "venv/Scripts/python.exe"), "entry": str(old_package / "entry.py"),
        "model_dir": str(data / "model"), "runtime_files": tree(version / "venv")}
    atomic_json(version / "preparation.json", prepared)
    config = initialize(data / "library", data / "model")
    Store(config).initialize_empty()
    libraries = [{"name": "kw-" + config.kb_id, "config": str(config.config_path)}]
    names = [item["name"] for item in libraries]
    before = cli.components(names)
    shutil.copytree(bundle / "marketplace", program / "marketplace")
    tx = program / "transactions/legacy"
    tx.mkdir(parents=True)
    cli.register(program / "marketplace", prepared["python"], prepared["entry"], libraries)
    receipt = {"schema_version": 1, "phase": "recovery_required", "old": None, "prepared": prepared,
        "transaction": str(tx), "codex_executable": str(args.codex), "codex_home": str(home), "libraries": libraries,
        "before_components": before, "last_components": cli.components(names), "observed_components": cli.components(names),
        "observed_marketplace_files": tree(program / "marketplace"), "unowned_hash": legacy_hash,
        "registry": str(data / "registry.json")}
    atomic_json(program / "pending-installation.json", receipt)
    execution = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(bundle / "install.py"),
        "--bundle", str(bundle), "--root", str(program), "--codex", str(args.codex), "--recover-pending"],
        text=True, encoding="utf-8", capture_output=True, timeout=120)
    assert execution.returncode == 0, (execution.stdout + execution.stderr)[-8000:]
    assert '"recovery_commit": "' + release["commit"] + '"' in execution.stdout, execution.stdout
    assert not (program / "pending-installation.json").exists()
    saved = json.loads((tx / "receipt.json").read_text(encoding="utf-8"))
    assert saved["unowned_hash"] == legacy_hash
    assert cli.config()["model"] == "synthetic-original"
    assert cli.components(names) == before
    assert config.config_path.is_file()
    result = {"ok": True, "bundle_commit": release["commit"], "new_recovery_code_used": True,
        "old_entry_not_invoked": True, "legacy_hash_preserved": True, "unowned_settings_preserved": True,
        "data_retained": True, "scope": "Native CLI and real minimal legacy interpreter; no user-global changes."}
args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
