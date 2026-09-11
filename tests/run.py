"""Run the public suite without relying on an editable or global installation."""
import sys
import unittest
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))
suite = (unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:]) if len(sys.argv) > 1
         else unittest.defaultTestLoader.discover(str(root / "tests"), pattern="test_*.py"))
result = unittest.TextTestRunner(verbosity=2, failfast=True).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
