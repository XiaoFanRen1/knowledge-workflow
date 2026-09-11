import tempfile
import unittest
from pathlib import Path

from knowledge_workflow.project import initialize
from knowledge_workflow.project_guard import evaluate, hook, configuration


class ProjectGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kw-hook-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        initialize(self.root, template="embedded")
        (self.root / "sdk").mkdir()
        (self.root / "work").mkdir()

    def test_protected_patch_is_denied_without_state_gates(self):
        event = {"tool_name": "apply_patch", "tool_input": {"patch": "*** Begin Patch\n*** Update File: sdk/file.h\n*** End Patch"}}
        self.assertEqual(hook(self.root, event)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_actual_subdirectory_cwd_is_used(self):
        event = {"tool_name": "exec_command", "cwd": str(self.root / "work"),
                 "tool_input": {"cmd": "echo x > ../sdk/file.h"}}
        self.assertEqual(evaluate(self.root, event)["decision"], "deny")

    def test_no_opinion_does_not_override_host_permissions(self):
        event = {"tool_name": "exec_command", "tool_input": {"cmd": "git status --short"}}
        self.assertEqual(hook(self.root, event), {})
        self.assertEqual(hook(self.root, {"tool_name": "host_browser_navigation", "tool_input": {}}), {})

    def test_configuration_is_only_a_preview(self):
        self.assertIn("PreToolUse", configuration(self.root)["hooks"])
        self.assertFalse((self.root / ".codex").exists())
