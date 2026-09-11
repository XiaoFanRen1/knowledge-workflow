"""Real local-model acceptance on synthetic data; never reads a personal corpus."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from .config import initialize
from .storage import Store
from .processes import command, environment
from . import jobs, runner


def packet(result):
    return result.structuredContent or json.loads(result.content[0].text)


async def exercise(model_dir):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    temp = tempfile.TemporaryDirectory(prefix="kw-semantic-smoke-")
    root = Path(temp.name).resolve()
    config = initialize(root / "private library", model_dir)
    Store(config).initialize_empty()
    runner.start(config)
    argv = command("mcp", "--config", config.config_path)
    parameters = StdioServerParameters(command=argv[0], args=argv[1:], env=environment())
    report = {"synthetic_only": True}
    job_id = None
    try:
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                saved = packet(await client.call_tool("capture_knowledge", {"operation_id": "synthetic-capture",
                    "title": "温室传感器的复位确认", "body": "温室传感器必须收到复位确认消息之后，才能清空采样环形缓冲区。超时情况下保留原始采样数据，并报告复位失败。"}))
                assert saved["ok"], saved
                started = time.perf_counter()
                submitted = packet(await client.call_tool("maintain_knowledge", {"operation_id": "synthetic-maintenance"}))
                assert submitted["ok"], submitted
                report["submit_seconds"] = time.perf_counter() - started
                job_id = submitted["job_id"]
        # The first client is gone. A new connection must still see the same task.
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as client:
                await client.initialize()
                deadline = time.monotonic() + 180
                while True:
                    state = packet(await client.call_tool("maintenance_status", {"job_id": job_id}))
                    if state["state"] in jobs.TERMINAL:
                        break
                    assert time.monotonic() < deadline, state
                    await asyncio.sleep(.5)
                assert state["state"] == "published", state
                report["generation"] = state["generation"]
                report["maintenance_seconds"] = time.perf_counter() - started
                timings = []
                for _ in range(3):
                    tick = time.perf_counter()
                    answer = packet(await client.call_tool("search", {"query": "什么时候允许清除传感器缓存里的测量数据？", "mode": "semantic"}))
                    assert answer["ok"] and answer["hits"], answer
                    if answer["stages"]["semantic"]["status"] != "used":
                        raise AssertionError(answer)
                    evidence = packet(await client.call_tool("read_evidence", {"evidence_id": answer["hits"][0]["evidence_id"]}))
                    assert "收到复位确认消息之后" in evidence["body"], evidence
                    timings.append({"seconds_to_body": time.perf_counter() - tick,
                                    "cold": answer["semantic_cold_start"], "result_state": answer["result_state"]})
                report["queries"] = timings
                report["status"] = packet(await client.call_tool("status", {}))
                repeated = packet(await client.call_tool("maintain_knowledge", {"operation_id": "synthetic-maintenance"}))
                assert repeated["job_id"] == job_id and repeated["duplicate"], repeated
        report["ok"] = True
        return report
    finally:
        if job_id:
            result = jobs.cancel(config, job_id)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                result = jobs.status(config, job_id)
                owned = [(result[k + "_pid"], result[k + "_created"])
                         for k in ("supervisor", "launcher") if result.get(k + "_pid")]
                if all(jobs._identity(pid, born) is False for pid, born in owned):
                    break
                await asyncio.sleep(.1)
            else:
                raise RuntimeError("test process cleanup unconfirmed: " + str(root))
        stopped = runner.stop(config)
        if not stopped["ok"]:
            raise RuntimeError("test runner cleanup unconfirmed")
        temp.cleanup()
