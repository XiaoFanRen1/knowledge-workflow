"""Delay fixture outside production code for real >60-second MCP acceptance."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from knowledge_workflow.config import KnowledgeConfig
from knowledge_workflow import jobs, runner
from knowledge_workflow.processes import command

parser = argparse.ArgumentParser()
parser.add_argument("--role", choices=("runner", "supervisor", "build"), required=True)
parser.add_argument("--config", type=Path, required=True)
parser.add_argument("--job-id")
args = parser.parse_args()
config = KnowledgeConfig.load(args.config)

def fixture_command(*values):
    role = {"job-supervise": "supervisor", "job-build": "build"}.get(values[0])
    if role:
        return [sys.executable, "-I", "-B", "-X", "utf8", str(Path(__file__).resolve()), "--role", role, *map(str, values[1:])]
    return command(*values)

if args.role == "runner":
    runner.command = fixture_command
    runner.serve(config)
elif args.role == "supervisor":
    jobs.command = fixture_command
    jobs.supervise(config, args.job_id)
else:
    time.sleep(65)
    jobs.execute_build(config, args.job_id)
