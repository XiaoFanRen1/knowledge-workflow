import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_workflow.cleanup import active_processes, transaction_inventory
from knowledge_workflow.distribution import sha256_file
from knowledge_workflow.installation import tree, recover_pending, prepare
from knowledge_workflow.util import atomic_json


class InstallationState(unittest.TestCase):
    def test_verified_runtime_can_retry_selftest_without_reinstallation(self):
        with tempfile.TemporaryDirectory(prefix="kw-resume-test-") as temporary:
            base = Path(temporary)
            root, data = base / "program", base / "data"
            version = root / "versions/0.1.0-test.1"
            (version / "venv").mkdir(parents=True)
            (version / "venv/owned.py").write_text("verified source", encoding="utf-8")
            atomic_json(root / ".knowledge-workflow-owner.json", {"schema_version": 1, "product": "knowledge-workflow", "root": str(root), "data": str(data)})
            record = {"state": "runtime_ready", "version": "0.1.0-test.1", "commit": "a" * 40,
                "python": str(version / "venv/python.exe"), "entry": str(version / "venv/entry.py"),
                "model_dir": str(data / "model"), "runtime_files": tree(version / "venv")}
            atomic_json(version / "preparation.json", record)
            with patch("knowledge_workflow.installation.verify_bundle", return_value={"version": record["version"], "commit": record["commit"]}), \
                 patch("knowledge_workflow.installation.verify_model"), patch("knowledge_workflow.installation.run", return_value="ok") as invoked:
                result = prepare(base / "bundle", root, data)
                self.assertEqual(result["state"], "prepared")
                self.assertEqual(invoked.call_count, 1)
                self.assertIn("self-test", invoked.call_args.args[0])
                (version / "venv/owned.py").write_text("user modification", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "modified"):
                    prepare(base / "bundle", root, data)

    def test_transaction_user_modification_blocks_cleanup(self):
        with tempfile.TemporaryDirectory(prefix="kw-transaction-test-") as temporary:
            root = Path(temporary)
            tx = root / "transactions/owned"
            tx.mkdir(parents=True)
            (tx / "previous.txt").write_text("reviewed", encoding="utf-8")
            atomic_json(tx / "receipt.json", {"phase": "recovered", "owned_files": tree(tx),
                "prepared": {"version": "0.1.0", "commit": "a" * 40}})
            self.assertEqual(len(transaction_inventory(root)), 1)
            (tx / "user-note.md").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "modified"):
                transaction_inventory(root)
            self.assertEqual((tx / "user-note.md").read_text(), "keep")

    def test_untrusted_transaction_cannot_leave_installation_root(self):
        with tempfile.TemporaryDirectory(prefix="kw-transaction-test-") as temporary:
            root = Path(temporary)
            atomic_json(root / "pending-installation.json", {"transaction": str(root.parent)})
            with self.assertRaisesRegex(ValueError, "untrusted"):
                recover_pending(root)

    def test_native_process_query_finds_the_current_interpreter(self):
        import os
        import sys
        self.assertIn(os.getpid(), active_processes(Path(sys._base_executable).parent))
        if sys.executable != sys._base_executable:
            self.assertIn(os.getppid(), active_processes(Path(sys.executable).parent.parent))
