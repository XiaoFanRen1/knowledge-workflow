"""Synthetic process protocol fixture, never installed as runtime code."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.worker_protocol import serve

parser = argparse.ArgumentParser()
parser.add_argument("--prepare-delay", type=float, default=0)
args = parser.parse_args()


def prepare(payload):
    time.sleep(args.prepare_delay)
    return {}


def execute(payload):
    if payload.get("query") == "delayed":
        time.sleep(1)
    return {"value": payload.get("query")}


serve(prepare, execute)
