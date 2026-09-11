"""Inject one native registration failure in an owned empty Codex home."""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.codex_integration import Codex
from knowledge_workflow.installation import activate, recover_pending
from knowledge_workflow.util import atomic_json
from knowledge_workflow.distribution import sha256_file

parser = argparse.ArgumentParser()
parser.add_argument("--codex", required=True, type=Path)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]

class FaultCodex(Codex):
    def call(self, *args):
        if args[:2] == ("plugin", "add") and "--help" not in args:
            raise RuntimeError("injected native registration failure")
        return super().call(*args)

with tempfile.TemporaryDirectory(prefix="kw-native-recovery-") as directory:
    root = Path(directory)
    bundle, program, data, home = [root / name for name in ("bundle", "program", "data", "codex")]
    shutil.copytree(source / ".agents", bundle / "marketplace/.agents")
    shutil.copytree(source / "plugins", bundle / "marketplace/plugins")
    (bundle / "runtime").mkdir()
    (bundle / "runtime/runtime.whl").write_bytes(b"synthetic metadata fixture; never executed")
    files = {p.relative_to(bundle).as_posix(): {"sha256": sha256_file(p), "bytes": p.stat().st_size}
             for p in bundle.rglob("*") if p.is_file()}
    atomic_json(bundle / "release.json", {"schema_version": 1, "commit": "a" * 40, "version": "0.1.0-test.1",
        "runtime_wheel": "runtime/runtime.whl", "files": files})
    version = program / "versions/0.1.0-test.1"
    prepared = {"version": "0.1.0-test.1", "commit": "a" * 40,
        "python": str(version / "venv/Scripts/python.exe"),
        "entry": str(version / "venv/Lib/site-packages/knowledge_workflow/entry.py"),
        "model_dir": str(root / "missing-model")}
    with patch("knowledge_workflow.installation.Codex", FaultCodex):
        try:
            activate(bundle, program, data, args.codex, home, prepared, startup=False)
        except RuntimeError as error:
            assert "injected" in str(error), error
        else:
            raise AssertionError("failure was not injected")
    assert (program / "pending-installation.json").is_file()
    codex = Codex(args.codex, home)
    assert codex.components([])["marketplace"] is not None
    result = recover_pending(program)
    assert result["ok"] and not (program / "pending-installation.json").exists()
    assert codex.components([])["marketplace"] is None
    assert (data / "library/knowledge.json").is_file()
    print(json.dumps({"ok": True, "native_partial_registration_recovered": True, "data_retained": True}))
