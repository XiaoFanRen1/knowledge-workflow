"""Invoke the same synthetic acceptance used by the installed runtime."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.validation import exercise

parser = argparse.ArgumentParser()
parser.add_argument("--model-dir", required=True, type=Path)
parser.add_argument("--output", type=Path)
args = parser.parse_args()
result = asyncio.run(exercise(args.model_dir.resolve()))
output = json.dumps(result, ensure_ascii=False, indent=2)
if args.output:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output + "\n", encoding="utf-8")
print(output)
