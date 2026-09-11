import tempfile
import unittest
from pathlib import Path

from knowledge_workflow.startup import install, remove
from knowledge_workflow.distribution import sha256_file


class Startup(unittest.TestCase):
    def test_owned_shortcut_lifecycle_in_explicit_fixture_directory(self):
        import sys
        with tempfile.TemporaryDirectory(prefix="kw-shortcut-test-") as temporary:
            root = Path(temporary)
            shortcut = root / "KnowledgeWorkflow.lnk"
            record = install(Path(sys.executable), root / "entry.py", root / "registry.json", target=shortcut)
            self.assertEqual(sha256_file(shortcut), record["sha256"])
            with self.assertRaisesRegex(ValueError, "modified_or_unowned"):
                install(Path(sys.executable), root / "entry.py", root / "registry.json", target=shortcut)
            remove(record)
            self.assertFalse(shortcut.exists())

    def test_modified_shortcut_is_not_removed(self):
        import sys
        with tempfile.TemporaryDirectory(prefix="kw-shortcut-test-") as temporary:
            root = Path(temporary)
            target = root / "owned.lnk"
            record = install(Path(sys.executable), root / "entry.py", root / "registry.json", target=target)
            target.write_bytes(b"user replacement")
            with self.assertRaisesRegex(ValueError, "modified"):
                remove(record)
            self.assertEqual(target.read_bytes(), b"user replacement")
