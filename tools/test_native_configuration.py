"""Native CLI regression with synthetic existing settings and legacy recovery records."""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.codex_integration import Codex
from knowledge_workflow.distribution import sha256_file
from knowledge_workflow.installation import activate, deactivate, recover_pending
from knowledge_workflow.util import atomic_json, read_json

parser = argparse.ArgumentParser()
parser.add_argument("--codex", required=True, type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
source = Path(__file__).resolve().parents[1]
rows = []
for scenario in ("empty-args", "legacy-recovery", "concurrent-unowned-edit"):
    with tempfile.TemporaryDirectory(prefix="kw-native-config-") as temporary:
        base = Path(temporary).resolve()
        bundle, program, data, home = [base / name for name in ("bundle", "program", "data", "codex")]
        home.mkdir()
        path = home / "config.toml"
        path.write_text('model = "synthetic-original"\n\n[mcp_servers.existing]\ncommand = "python.exe"\nargs = []\n', encoding="utf-8")
        cli = Codex(args.codex, home)
        original = cli.unowned_hash([])
        legacy = cli.unowned_hash([], version=1)
        shutil.copytree(source / ".agents", bundle / "marketplace/.agents")
        shutil.copytree(source / "plugins", bundle / "marketplace/plugins")
        (bundle / "runtime").mkdir()
        (bundle / "runtime/runtime.whl").write_bytes(b"synthetic; never executed")
        records = {p.relative_to(bundle).as_posix(): {"bytes": p.stat().st_size, "sha256": sha256_file(p)}
                   for p in bundle.rglob("*") if p.is_file()}
        atomic_json(bundle / "release.json", {"schema_version": 1, "commit": "a" * 40, "version": "0.1.0-test",
            "runtime_wheel": "runtime/runtime.whl", "files": records})
        version = program / "versions/0.1.0-test"
        prepared = {"version": "0.1.0-test", "commit": "a" * 40,
            "python": str(version / "venv/Scripts/python.exe"),
            "entry": str(version / "venv/Lib/site-packages/knowledge_workflow/entry.py"), "model_dir": str(base / "model")}
        if scenario == "empty-args":
            with patch("knowledge_workflow.installation.start_selected_runners", return_value=""):
                result = activate(bundle, program, data, args.codex, home, prepared, startup=False)
            assert result["ok"]
            state = read_json(program / "installation.json")
            names = [item["name"] for item in read_json(data / "registry.json")["libraries"].values()]
            assert cli.unowned_hash(names) == original
            assert "args" not in cli.config()["mcp_servers"]["existing"]
            deactivate(program)
            assert cli.unowned_hash(names) == original
        else:
            with patch("knowledge_workflow.installation.start_selected_runners", side_effect=RuntimeError("synthetic interrupted activation")):
                try:
                    activate(bundle, program, data, args.codex, home, prepared, startup=False)
                except RuntimeError as error:
                    assert "synthetic interrupted" in str(error), error
                else:
                    raise AssertionError("interruption not injected")
            pending = program / "pending-installation.json"
            record = read_json(pending)
            if scenario == "legacy-recovery":
                record["unowned_hash"] = legacy
                record.pop("unowned_hash_version")
                atomic_json(pending, record)
            else:
                raw = path.read_text(encoding="utf-8")
                assert '"synthetic-original"' in raw
                path.write_text(raw.replace('"synthetic-original"', '"synthetic-user-change"'), encoding="utf-8")
            current = cli.unowned_hash([item["name"] for item in record["libraries"]])
            result = recover_pending(program)
            assert result["ok"] and not pending.exists()
            assert cli.unowned_hash([]) == current
            saved = read_json(Path(record["transaction"]) / "receipt.json")
            assert saved["unowned_hash"] == record["unowned_hash"]
            if scenario == "concurrent-unowned-edit":
                assert cli.config()["model"] == "synthetic-user-change"
        assert (data / "library/knowledge.json").is_file()
        rows.append({"scenario": scenario, "ok": True, "data_retained": True})
result = {"ok": True, "scenarios": rows, "boundary": "Native configuration/transaction test; dummy runtime paths are never invoked."}
if args.output:
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
