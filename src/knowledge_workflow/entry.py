"""Absolute-path launcher, also usable with Python -I from an installed wheel."""
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from knowledge_workflow.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
