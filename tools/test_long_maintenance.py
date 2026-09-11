"""A real MCP client disconnects while a maintenance task runs beyond 60 seconds."""
import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "src"))
from knowledge_workflow.config import initialize
from knowledge_workflow.storage import Store
from knowledge_workflow.processes import command, environment
from knowledge_workflow import jobs, runner
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

def packet(value):
    return value.structuredContent or json.loads(value.content[0].text)

async def run(model):
    with tempfile.TemporaryDirectory(prefix="kw-long-maintenance-") as temporary:
        directory = Path(temporary)
        config = initialize(directory / "library", model)
        Store(config).initialize_empty()
        with (directory / "runner.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, "-I", "-B", "-X", "utf8", str(root / "tests/slow_runner_fixture.py"),
                "--role", "runner", "--config", str(config.config_path)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                env=environment(), creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 5
            while runner.status(config)["state"] != "ready":
                assert process.poll() is None and time.monotonic() < deadline
                await asyncio.sleep(.05)
            argv = command("mcp", "--config", config.config_path)
            params = StdioServerParameters(command=argv[0], args=argv[1:], env=environment())
            jid = None
            try:
                async with stdio_client(params, errlog=log) as streams:
                    async with ClientSession(*streams) as client:
                        await client.initialize()
                        saved = packet(await client.call_tool("capture_knowledge", {"operation_id": "long-capture",
                            "title": "Reservoir valve", "body": "The reservoir valve opens after a confirmed safety acknowledgement."}))
                        assert saved["ok"]
                        start = time.monotonic()
                        job = packet(await client.call_tool("maintain_knowledge", {"operation_id": "long-maintenance"}))
                        submitted = time.monotonic() - start
                        assert submitted < 2 and job["ok"], job
                        jid = job["job_id"]
                async with stdio_client(params, errlog=log) as streams:
                    async with ClientSession(*streams) as client:
                        await client.initialize()
                        while True:
                            state = packet(await client.call_tool("maintenance_status", {"job_id": jid}))
                            if state["state"] in jobs.TERMINAL:
                                break
                            assert time.monotonic() - start < 180
                            await asyncio.sleep(5)
                        elapsed = time.monotonic() - start
                        assert state["state"] == "published" and elapsed > 60, state
                        answer = packet(await client.call_tool("search", {"query": "reservoir valve acknowledgement", "mode": "semantic"}))
                        assert answer["stages"]["semantic"]["status"] == "used", answer
                        evidence = packet(await client.call_tool("read_evidence", {"evidence_id": answer["hits"][0]["evidence_id"]}))
                        assert "confirmed safety acknowledgement" in evidence["body"]
                        return {"ok": True, "submission_seconds": submitted, "maintenance_seconds": elapsed,
                                "client_reconnected": True, "semantic_body_read": True}
            finally:
                if jid:
                    jobs.cancel(config, jid)
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        state = jobs.status(config, jid)
                        identities = [(state[k + "_pid"], state[k + "_created"]) for k in ("supervisor", "launcher") if state.get(k + "_pid")]
                        if all(jobs._identity(pid, born) is False for pid, born in identities):
                            break
                        await asyncio.sleep(.1)
                assert runner.stop(config)["ok"]
                process.wait(timeout=5)

parser = argparse.ArgumentParser()
parser.add_argument("--model-dir", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args = parser.parse_args()
result = asyncio.run(run(args.model_dir))
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
