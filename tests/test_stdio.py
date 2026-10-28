import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cookbook_demo.importer import build
from library_v2.service import SnapshotService
from test_public_import import RAW, PROFILE


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Install the server extra for MCP tests")
class StdioTests(unittest.TestCase):
    def test_four_tools_match_direct_service_and_publish_contracts(self):
        from cookbook_demo.stdio import create_server
        with tempfile.TemporaryDirectory() as tmp, patch("cookbook_demo.importer.profiles", return_value=[PROFILE]):
            path = Path(tmp)
            (path / "123.html").write_bytes(RAW)
            build(path / "library", path, offline=True)
            service = SnapshotService(path / "library/library.sqlite", "x" * 32)
            async def run():
                server = create_server(service)
                tools = await server.list_tools()
                self.assertEqual({tool.name for tool in tools}, {"search_recipes", "get_recipes", "search_knowledge", "get_passages"})
                for tool in tools:
                    self.assertNotIn("$ref", str(tool.input_schema))
                    self.assertIn("items", tool.output_schema["properties"])
                result = await server.call_tool("search_recipes", {"query": "SOUP"})
                self.assertEqual(result.structured_content, service.search_recipes(query="SOUP"))
                rid = result.structured_content["items"][0]["id"]
                read = await server.call_tool("get_recipes", {"recipe_ids": [rid]})
                self.assertEqual(read.structured_content, service.get_recipes([rid]))
                found = await server.call_tool("search_knowledge", {"query": "clean"})
                pid = found.structured_content["items"][0]["id"]
                passage = await server.call_tool("get_passages", {"passage_ids": [pid]})
                self.assertEqual(passage.structured_content, service.get_passages([pid]))
            try:
                asyncio.run(run())
            finally:
                service.close()
