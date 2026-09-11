"""Absolute-path launcher, also usable with Python -I from an installed wheel."""
import sys
from pathlib import Path

if __package__ in {None, ""}:
    package_parent = str(Path(__file__).resolve().parent.parent)
    sys.path[:] = [value for value in sys.path if value != package_parent]
    sys.path.insert(0, package_parent)

from knowledge_workflow.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
