import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_workflow.distribution import verify_bundle
from knowledge_workflow.installation import validate_roots, preview
from knowledge_workflow.util import atomic_json, digest


class Distribution(unittest.TestCase):
    def test_preview_discloses_model_and_sources_without_creating_targets(self):
        with tempfile.TemporaryDirectory(prefix="kw-preview-test-") as directory:
            base = Path(directory).resolve()
            manifest = {"version": "test", "commit": "a" * 40, "files": {"wheels/example.whl": {}}}
            with patch("knowledge_workflow.installation.verify_bundle", return_value=manifest):
                result = preview(base / "bundle", base / "program", base / "data", base / "codex.exe", base / "codex-home")
            self.assertEqual(list(base.iterdir()), [])
            self.assertEqual(result["model"]["acquisition"], "download_pinned_public_files")
            self.assertEqual(result["model"]["files"], 9)
            self.assertGreater(result["model"]["bytes"], 100_000_000)
            self.assertEqual(result["dependencies"]["bundled_wheels"], 1)
            self.assertEqual(result["knowledge"]["new_library_sources"], [str(base / "data/library/notes")])
            self.assertIn("kw-<new-stable-library-id>", result["registrations"]["mcp_names"])

    def test_offline_preview_reports_a_local_copy_or_missing_files(self):
        with tempfile.TemporaryDirectory(prefix="kw-preview-test-") as directory:
            base = Path(directory).resolve()
            manifest = {"version": "test", "commit": "a" * 40, "files": {}}
            with patch("knowledge_workflow.installation.verify_bundle", return_value=manifest):
                missing = preview(base / "bundle", base / "program", base / "data", base / "codex.exe", base / "codex-home", offline=True)
                local = preview(base / "bundle", base / "program", base / "data", base / "codex.exe", base / "codex-home", offline=True, model_source=base / "local-model")
            self.assertEqual(missing["model"]["acquisition"], "offline_files_required")
            self.assertEqual(local["model"]["acquisition"], "copy_verified_local_files")
            self.assertIsNone(local["model"]["download_origin"])
            self.assertEqual(list(base.iterdir()), [])

    def test_partial_model_does_not_prevent_previewing_a_download_resume(self):
        with tempfile.TemporaryDirectory(prefix="kw-preview-test-") as directory:
            base = Path(directory).resolve()
            model = base / "data/models/fixed-revision"
            model.mkdir(parents=True)
            (model / "first").write_bytes(b"a")
            manifest = {"version": "test", "commit": "a" * 40, "files": {}}
            spec = {"model": "synthetic", "revision": "fixed-revision", "files": [
                {"path": "first", "bytes": 1}, {"path": "second", "bytes": 1}]}
            with patch("knowledge_workflow.installation.verify_bundle", return_value=manifest), \
                    patch("knowledge_workflow.distribution.model_manifest", return_value=spec):
                result = preview(base / "bundle", base / "program", base / "data", base / "codex.exe", base / "codex-home")
            self.assertEqual(result["model"]["acquisition"], "download_pinned_public_files")
            self.assertEqual(result["model"]["files_present"], 1)
            self.assertEqual((model / "first").read_bytes(), b"a")
            self.assertFalse((model / "second").exists())

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
