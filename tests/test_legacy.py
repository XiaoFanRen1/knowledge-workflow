import unittest
from pathlib import Path

from knowledge_workflow.build import build
from knowledge_workflow.config import KnowledgeConfig, SourceRoot
from knowledge_workflow.legacy import import_generation, import_feedback
from knowledge_workflow.service import KnowledgeService
from knowledge_workflow.storage import Store
from knowledge_workflow.util import atomic_json, digest
from support import Fixture


class Legacy(unittest.TestCase):
    def test_remaps_sources_and_keeps_original_evidence_ids_and_feedback(self):
        original, target = Fixture(), Fixture()
        self.addCleanup(original.close)
        self.addCleanup(target.close)
        original.capture()
        build(original.config, lexical_only=True)
        answer = original.service.search("garden sensor", mode="lexical")
        eid = answer["hits"][0]["evidence_id"]
        original.service.record_feedback("old-feedback", answer["query_id"], "useful", evidence_id=eid)
        selected = Store(original.config).load()
        database_before = digest(selected.sqlite.read_bytes())
        binding = KnowledgeConfig(target.config.config_path, target.config.kb_id, target.config.root,
            target.config.cache, target.config.model_dir,
            target.config.sources + (SourceRoot("legacy", original.config.root),))
        atomic_json(binding.config_path, binding.json())
        import_generation(binding, selected.sqlite, selected.directory / "manifest.json",
                          [{"prefix": "", "source_id": "legacy"}], publish=True)
        import_feedback(binding, original.config.cache / "control.sqlite")
        service = KnowledgeService(binding)
        self.addCleanup(service.close)
        evidence = service.read_evidence(eid)
        self.assertTrue(evidence["ok"], evidence)
        self.assertTrue(evidence["path"].startswith("legacy/notes/"))
        self.assertIn("confirmed reset acknowledgement", evidence["body"])
        self.assertEqual(evidence["source_state"], "unchanged")
        self.assertEqual(service.status()["feedback_count"], 1)
        self.assertEqual(digest(selected.sqlite.read_bytes()), database_before)

    def test_import_requires_explicit_registered_source_mapping(self):
        original, target = Fixture(), Fixture()
        self.addCleanup(original.close)
        self.addCleanup(target.close)
        original.capture()
        build(original.config, lexical_only=True)
        selected = Store(original.config).load()
        with self.assertRaises(ValueError):
            import_generation(target.config, selected.sqlite, selected.directory / "manifest.json", [])
        self.assertEqual(Store(target.config).load().manifest["source_count"], 0)
