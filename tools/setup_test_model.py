"""Explicit pinned model setup for synthetic tests and CI."""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.distribution import acquire_model

parser = argparse.ArgumentParser()
parser.add_argument("--destination", required=True, type=Path)
parser.add_argument("--source", type=Path)
args = parser.parse_args()
print(json.dumps(acquire_model(args.destination, existing=args.source)))
