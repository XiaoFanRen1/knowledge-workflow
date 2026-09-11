import unittest
from pathlib import Path

from knowledge_workflow.build import build
from knowledge_workflow.config import KnowledgeConfig
from knowledge_workflow.recovery import backup, restore, verify
from knowledge_workflow.service import KnowledgeService
from support import Fixture


class Recovery(unittest.TestCase):
    def test_independent_restore_keeps_historical_feedback_body(self):
        f = Fixture()
        self.addCleanup(f.close)
        saved = f.capture()
        build(f.config, lexical_only=True)
        answer = f.service.search("garden sensor", mode="lexical")
        old = answer["hits"][0]["evidence_id"]
        f.service.record_feedback("history", answer["query_id"], "useful", evidence_id=old)
        f.capture("updated", record_id=saved["record_id"], expected_hash=saved["sha256"], body="The new sensor uses a separate reset transaction.")
        build(f.config, lexical_only=True)
        snapshot = f.root / "snapshot"
        backup(f.config, snapshot)
        verify(snapshot)
        report = restore(snapshot, f.root / "restored", f.config.model_dir)
        restored = KnowledgeConfig.load(report["config"])
        self.assertTrue(restored.notes.is_relative_to(f.root / "restored"))
        service = KnowledgeService(restored)
        self.addCleanup(service.close)
        Path(saved["path"]).unlink()
        evidence = service.read_evidence(old)
        self.assertTrue(evidence["ok"], evidence)
        self.assertIn("confirmed reset acknowledgement", evidence["body"])
        self.assertEqual(evidence["source_state"], "changed_since_index")
        self.assertEqual(report["feedback_evidence_read"], 1)
        current = service.search("separate reset transaction", mode="lexical")
        self.assertEqual(current["hits"][0]["source_state"], "unchanged")

    def test_tampering_and_unsafe_destination_rejected(self):
        f = Fixture()
        self.addCleanup(f.close)
        f.capture()
        build(f.config, lexical_only=True)
        with self.assertRaises(ValueError):
            backup(f.config, f.config.notes / "snapshot")
        snapshot = f.root / "snapshot"
        backup(f.config, snapshot)
        target = next((snapshot / "sources/notes").glob("*.md"))
        target.write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            restore(snapshot, f.root / "restored", f.config.model_dir)
        self.assertFalse((f.root / "restored").exists())
