import tempfile
import unittest
from pathlib import Path

from knowledge_workflow.project import initialize
from knowledge_workflow.util import read_json


class Project(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kw-project-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_preserves_existing_instructions_and_is_idempotent(self):
        original = "# Local rules\r\n保留这段规则。\r\n".encode("utf-8")
        agents = self.root / "AGENTS.md"
        agents.write_bytes(original)
        first = initialize(self.root, template="python")
        self.assertTrue(agents.read_bytes().startswith(original))
        self.assertTrue(initialize(self.root, template="python")["unchanged"])
        profile = read_json(self.root / ".knowledge-workflow/project.json")
        self.assertNotIn("chip", profile)
        self.assertFalse(first["hooks_enabled"])

    def test_user_changes_and_collisions_are_preserved(self):
        initialize(self.root)
        profile = self.root / ".knowledge-workflow/project.json"
        profile.write_text("{\"user\": true}", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "modified"):
            initialize(self.root)
        self.assertEqual(profile.read_text(encoding="utf-8"), '{"user": true}')

    def test_dry_run_does_not_create_files(self):
        result = initialize(self.root, template="embedded", dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_shared_reference_cannot_contain_private_path(self):
        with self.assertRaises(ValueError):
            initialize(self.root, knowledge_ref="C:/private/library")
        self.assertEqual(list(self.root.iterdir()), [])
