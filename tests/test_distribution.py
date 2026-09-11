import json
import tempfile
import unittest
from pathlib import Path

from knowledge_workflow.distribution import verify_bundle
from knowledge_workflow.installation import validate_roots
from knowledge_workflow.util import atomic_json, digest


class Distribution(unittest.TestCase):
    def test_nested_manifest_name_cannot_hide_extra_payload(self):
        with tempfile.TemporaryDirectory(prefix="kw-bundle-test-") as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime/runtime.whl").write_bytes(b"synthetic")
            atomic_json(root / "release.json", {"schema_version": 1, "commit": "a" * 40,
                "runtime_wheel": "runtime/runtime.whl", "files": {"runtime/runtime.whl": {"bytes": 9, "sha256": digest(b"synthetic")}}})
            verify_bundle(root)
            (root / "runtime/release.json").write_text("unreviewed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "inventory"):
                verify_bundle(root)

    def test_modified_runtime_payload_fails_hash_check(self):
        with tempfile.TemporaryDirectory(prefix="kw-bundle-test-") as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            target = root / "runtime/runtime.whl"
            target.write_bytes(b"modified!")
            atomic_json(root / "release.json", {"schema_version": 1, "commit": "a" * 40,
                "runtime_wheel": "runtime/runtime.whl", "files": {"runtime/runtime.whl": {"bytes": 9, "sha256": digest(b"synthetic")}}})
            with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                verify_bundle(root)

    def test_program_and_knowledge_roots_cannot_overlap(self):
        with tempfile.TemporaryDirectory(prefix="kw-roots-test-") as directory:
            root = Path(directory)
            for first, second in ((root, root), (root, root / "data"), (root / "program", root)):
                with self.assertRaises(ValueError):
                    validate_roots(first, second)
