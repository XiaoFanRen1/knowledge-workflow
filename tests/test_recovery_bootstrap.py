import importlib.util
import tempfile
import unittest
from pathlib import Path

from knowledge_workflow.installation import tree
from knowledge_workflow.util import atomic_json

spec = importlib.util.spec_from_file_location("kw_install_bootstrap_test", Path(__file__).resolve().parents[1] / "install.py")
bootstrap = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bootstrap)


class RecoveryBootstrap(unittest.TestCase):
    def fixture(self, temporary):
        root = Path(temporary).resolve() / "program"
        version = root / "versions/0.1.0-old"
        python = version / "venv/Scripts/python.exe"
        entry = version / "venv/Lib/site-packages/knowledge_workflow/entry.py"
        python.parent.mkdir(parents=True)
        entry.parent.mkdir(parents=True)
        python.write_bytes(b"synthetic interpreter identity; never executed")
        entry.write_text("raise RuntimeError('old code')", encoding="utf-8")
        record = {"version": "0.1.0-old", "state": "prepared", "commit": "a" * 40,
            "python": str(python), "entry": str(entry), "runtime_files": tree(version / "venv")}
        atomic_json(version / "preparation.json", record)
        return root, record

    def test_verified_existing_runtime_can_supply_recovery_dependencies(self):
        with tempfile.TemporaryDirectory(prefix="kw-bootstrap-test-") as temporary:
            root, record = self.fixture(temporary)
            self.assertEqual(bootstrap.validated_recovery_runtime(root, record), Path(record["python"]))

    def test_outside_interpreter_or_invalid_version_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="kw-bootstrap-test-") as temporary:
            root, record = self.fixture(temporary)
            with self.assertRaisesRegex(ValueError, "runtime_path"):
                bootstrap.validated_recovery_runtime(root, {**record, "python": str(root.parent / "outside.exe")})
            for version in ("..", "../outside", "C:/outside"):
                with self.subTest(version=version), self.assertRaisesRegex(ValueError, "recovery_version"):
                    bootstrap.validated_recovery_runtime(root, {**record, "version": version})

    def test_modified_runtime_or_record_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory(prefix="kw-bootstrap-test-") as temporary:
            root, record = self.fixture(temporary)
            with self.assertRaisesRegex(ValueError, "runtime_record"):
                bootstrap.validated_recovery_runtime(root, {**record, "commit": "b" * 40})
            Path(record["entry"]).write_text("user change", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime_modified"):
                bootstrap.validated_recovery_runtime(root, record)
