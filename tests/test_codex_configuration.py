import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_workflow.codex_integration import Codex
from knowledge_workflow.installation import activate, recover_pending
from knowledge_workflow.util import read_json


class MemoryCodex(Codex):
    value = {}
    mutate_model = False
    def __init__(self, executable, home):
        self.executable, self.home = Path(executable), Path(home)
    def capabilities(self):
        return "synthetic"
    def config(self):
        return copy.deepcopy(type(self).value)
    def register(self, marketplace_root, python, entry, libraries):
        value = type(self).value
        value.setdefault("marketplaces", {})["knowledge-workflow"] = {"source_type": "local", "source": str(marketplace_root)}
        value.setdefault("plugins", {})["knowledge-workflow@knowledge-workflow"] = {"enabled": True}
        for item in libraries:
            value.setdefault("mcp_servers", {})[item["name"]] = {"command": str(python),
                "args": ["-I", "-B", "-X", "utf8", str(entry), "mcp", "--config", item["config"]]}
        for server in value["mcp_servers"].values():
            if server.get("args") == []:
                server.pop("args")
        if type(self).mutate_model:
            value["model"] = "concurrent-user-choice"
    def remove(self, names):
        value = type(self).value
        for name in names:
            value.get("mcp_servers", {}).pop(name, None)
        value.get("plugins", {}).pop("knowledge-workflow@knowledge-workflow", None)
        value.get("marketplaces", {}).pop("knowledge-workflow", None)


class CodexConfiguration(unittest.TestCase):
    def setUp(self):
        MemoryCodex.value = {"model": "original-choice", "mcp_servers": {"existing": {"command": "python.exe", "args": []}}}
        MemoryCodex.mutate_model = False
        self.codex = MemoryCodex(Path("codex.exe"), Path("codex-home"))

    def fixture(self, directory):
        root = Path(directory).resolve()
        bundle, program, data, home = [root / name for name in ("bundle", "program", "data", "codex")]
        (bundle / "marketplace").mkdir(parents=True)
        (bundle / "marketplace/owned.json").write_text("{}", encoding="utf-8")
        version = "0.1.0-test"
        prepared = {"version": version, "commit": "a" * 40,
            "python": str(program / "versions" / version / "venv/Scripts/python.exe"),
            "entry": str(program / "versions" / version / "venv/Lib/site-packages/knowledge_workflow/entry.py"),
            "model_dir": str(root / "model")}
        manifest = {"version": version, "commit": "a" * 40}
        return bundle, program, data, home, prepared, manifest

    def test_only_empty_stdio_arguments_are_equivalent(self):
        baseline = self.codex.unowned_hash([])
        legacy = self.codex.unowned_hash([], version=1)
        MemoryCodex.value["mcp_servers"]["existing"].pop("args")
        self.assertEqual(self.codex.unowned_hash([]), baseline)
        self.assertNotEqual(self.codex.unowned_hash([], version=1), legacy)
        self.assertEqual(MemoryCodex.value["model"], "original-choice")

    def test_nonempty_arguments_and_other_fields_still_change_fingerprint(self):
        original = copy.deepcopy(MemoryCodex.value)
        baseline = self.codex.unowned_hash([])
        for field, value in (("args", ["--version"]), ("env", {}), ("cwd", "."), ("enabled", True)):
            MemoryCodex.value = copy.deepcopy(original)
            MemoryCodex.value["mcp_servers"]["existing"][field] = value
            self.assertNotEqual(self.codex.unowned_hash([]), baseline, field)
        MemoryCodex.value = copy.deepcopy(original)
        MemoryCodex.value["model"] = "new-choice"
        self.assertNotEqual(self.codex.unowned_hash([]), baseline)

    def test_http_or_ambiguous_transport_is_not_normalized(self):
        for server in ({"url": "https://example.invalid", "args": []},
                       {"url": "https://example.invalid", "command": "python.exe", "args": []},
                       {"command": "", "args": []}):
            MemoryCodex.value = {"mcp_servers": {"existing": server}}
            before = self.codex.unowned_hash([])
            server.pop("args")
            self.assertNotEqual(self.codex.unowned_hash([]), before)

    def test_seconds_numeric_representation_is_equivalent_but_values_are_not(self):
        server = MemoryCodex.value["mcp_servers"]["existing"]
        server.update(startup_timeout_sec=17, tool_timeout_sec=61)
        before = self.codex.unowned_hash([])
        server.update(startup_timeout_sec=17.0, tool_timeout_sec=61.0)
        self.assertEqual(self.codex.unowned_hash([]), before)
        server["startup_timeout_sec"] = 18.0
        self.assertNotEqual(self.codex.unowned_hash([]), before)
        server["startup_timeout_sec"] = 17.25
        self.assertNotEqual(self.codex.unowned_hash([]), before)

    def test_bools_and_unknown_numeric_fields_remain_distinct(self):
        server = MemoryCodex.value["mcp_servers"]["existing"]
        server["startup_timeout_sec"] = 1
        before = self.codex.unowned_hash([])
        server["startup_timeout_sec"] = True
        self.assertNotEqual(self.codex.unowned_hash([]), before)
        server["startup_timeout_sec"] = 1
        server["unknown_seconds"] = 5
        before = self.codex.unowned_hash([])
        server["unknown_seconds"] = 5.0
        self.assertNotEqual(self.codex.unowned_hash([]), before)

    def test_actual_unrelated_change_still_blocks_activation_and_recovery_preserves_it(self):
        with tempfile.TemporaryDirectory(prefix="kw-config-recovery-") as directory:
            bundle, program, data, home, prepared, manifest = self.fixture(directory)
            MemoryCodex.mutate_model = True
            with patch("knowledge_workflow.installation.Codex", MemoryCodex), \
                    patch("knowledge_workflow.installation.verify_bundle", return_value=manifest):
                with self.assertRaisesRegex(RuntimeError, "unowned_codex_configuration_changed"):
                    activate(bundle, program, data, Path("codex.exe"), home, prepared, startup=False)
                old_hash = read_json(program / "pending-installation.json")["unowned_hash"]
                result = recover_pending(program)
            self.assertTrue(result["current_unowned_config_preserved"])
            self.assertEqual(MemoryCodex.value["model"], "concurrent-user-choice")
            self.assertEqual(MemoryCodex.value["mcp_servers"], {"existing": {"command": "python.exe"}})
            receipt = read_json(next((program / "transactions").glob("*/receipt.json")))
            self.assertEqual(receipt["unowned_hash"], old_hash)
            self.assertEqual(receipt["recovery_unowned_hash_version"], 2)

    def test_legacy_receipt_is_recovered_without_rewriting_its_old_hash(self):
        with tempfile.TemporaryDirectory(prefix="kw-config-recovery-") as directory:
            bundle, program, data, home, prepared, manifest = self.fixture(directory)
            legacy = self.codex.unowned_hash([], version=1)
            with patch("knowledge_workflow.installation.Codex", MemoryCodex), \
                    patch("knowledge_workflow.installation.verify_bundle", return_value=manifest), \
                    patch("knowledge_workflow.installation.start_selected_runners", side_effect=RuntimeError("injected stop")):
                with self.assertRaisesRegex(RuntimeError, "injected stop"):
                    activate(bundle, program, data, Path("codex.exe"), home, prepared, startup=False)
            pending = program / "pending-installation.json"
            record = read_json(pending)
            record["unowned_hash"] = legacy
            record.pop("unowned_hash_version")
            pending.write_text(json.dumps(record), encoding="utf-8")
            with patch("knowledge_workflow.installation.Codex", MemoryCodex):
                self.assertTrue(recover_pending(program)["ok"])
            self.assertFalse(pending.exists())
            result = read_json(next((program / "transactions").glob("*/receipt.json")))
            self.assertEqual(result["unowned_hash"], legacy)
            self.assertEqual(MemoryCodex.value["model"], "original-choice")

    def test_user_modified_owned_registration_stops_recovery(self):
        with tempfile.TemporaryDirectory(prefix="kw-config-recovery-") as directory:
            bundle, program, data, home, prepared, manifest = self.fixture(directory)
            with patch("knowledge_workflow.installation.Codex", MemoryCodex), \
                    patch("knowledge_workflow.installation.verify_bundle", return_value=manifest), \
                    patch("knowledge_workflow.installation.start_selected_runners", side_effect=RuntimeError("injected stop")):
                with self.assertRaisesRegex(RuntimeError, "injected stop"):
                    activate(bundle, program, data, Path("codex.exe"), home, prepared, startup=False)
            record = read_json(program / "pending-installation.json")
            name = record["libraries"][0]["name"]
            MemoryCodex.value["mcp_servers"][name]["args"].append("--user-change")
            before = copy.deepcopy(MemoryCodex.value)
            with patch("knowledge_workflow.installation.Codex", MemoryCodex):
                with self.assertRaisesRegex(ValueError, "codex_changed"):
                    recover_pending(program)
            self.assertEqual(MemoryCodex.value, before)
