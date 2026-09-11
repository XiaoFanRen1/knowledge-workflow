import asyncio
import json
import unittest

from knowledge_workflow.build import build
from knowledge_workflow.processes import command, environment
from support import Fixture


def structured(result):
    return result.structuredContent or json.loads(result.content[0].text)


class MCP(unittest.TestCase):
    def test_real_stdio_capture_search_read_feedback_and_status(self):
        fixture = Fixture()
        self.addCleanup(fixture.close)
        async def exercise():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            argv = command("mcp", "--config", fixture.config.config_path)
            params = StdioServerParameters(command=argv[0], args=argv[1:], env=environment())
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    exposed = (await client.list_tools()).tools
                    self.assertEqual({t.name for t in exposed}, {"search", "read_evidence", "record_feedback", "status",
                        "capture_knowledge", "maintain_knowledge", "maintenance_status", "cancel_maintenance"})
                    flags = {t.name: t.annotations.readOnlyHint for t in exposed}
                    self.assertFalse(flags["capture_knowledge"])
                    self.assertTrue(flags["maintenance_status"])
                    saved = structured(await client.call_tool("capture_knowledge", {"operation_id": "stdio-capture",
                        "title": "Orchard sensor", "body": "The orchard sensor acknowledges reset before clearing its sample ring."}))
                    self.assertTrue(saved["ok"], saved)
                    await asyncio.to_thread(build, fixture.config, lexical_only=True)
                    answer = structured(await client.call_tool("search", {"query": "orchard reset", "mode": "lexical"}))
                    self.assertTrue(answer["hits"], answer)
                    evidence = structured(await client.call_tool("read_evidence", {"evidence_id": answer["hits"][0]["evidence_id"]}))
                    self.assertIn("acknowledges reset", evidence["body"])
                    event = {"event_id": "stdio-event", "query_id": answer["query_id"], "outcome": "useful", "evidence_id": evidence["evidence_id"]}
                    self.assertTrue(structured(await client.call_tool("record_feedback", event))["recorded"])
                    self.assertTrue(structured(await client.call_tool("record_feedback", event))["duplicate"])
                    self.assertFalse(structured(await client.call_tool("record_feedback", {**event, "outcome": "missing"}))["ok"])
                    state = structured(await client.call_tool("status", {}))
                    self.assertEqual(state["feedback_count"], 1)
                    self.assertEqual(state["pending_capture_count"], 0)
        asyncio.run(exercise())
