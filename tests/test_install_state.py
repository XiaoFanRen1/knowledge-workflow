import tempfile
import unittest
from pathlib import Path

from knowledge_workflow.cleanup import active_processes, transaction_inventory
from knowledge_workflow.distribution import sha256_file
from knowledge_workflow.installation import tree, recover_pending
from knowledge_workflow.util import atomic_json


class InstallationState(unittest.TestCase):
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
