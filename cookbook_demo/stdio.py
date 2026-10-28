"""Local MCP adapter. No listening socket or hosted account is required."""
from typing import Any
from mcp.server import MCPServer
from library_v2.transport import LIBRARY_INSTRUCTIONS, tool_schema, dispatch
from library_v2.contracts import validate_input, validate_output


def create_server(service):
    class ContractServer(MCPServer):
        async def list_tools(self):
            tools = await super().list_tools()
            for tool in tools:
                tool.input_schema = tool_schema(tool.name)
                tool.output_schema = tool_schema(tool.name, output=True)
            return tools

        async def call_tool(self, name, arguments, context=None):
            from mcp.types import CallToolResult, TextContent
            validate_input(name, arguments)
            payload = dispatch(service, name, arguments)
            validate_output(payload)
            summary = "Evidence page delivered; follow next_cursor." if payload["next_cursor"] else "Evidence response delivered."
            return CallToolResult(content=[TextContent(type="text", text=summary)], structured_content=payload)

    server = ContractServer("Cookbook Library", instructions=LIBRARY_INSTRUCTIONS)

    @server.tool()
    def search_recipes(query: str | None = None, filters: dict[str, Any] | None = None,
                       preferences: list[str] | None = None, limit: int = 20, cursor: str | None = None) -> dict:
        """Discover recipe candidates; read selected IDs before using their source text. Public demo recipes retain prose and do not have structured ingredient coverage."""
        return service.search_recipes(query=query, filters=filters, preferences=preferences, limit=limit, cursor=cursor)

    @server.tool()
    def get_recipes(recipe_ids: list[str], include_dependencies: bool = True, cursor: str | None = None) -> dict:
        """Read recipes with citations and qualifications. Follow all continuations with unchanged arguments; complete delivery does not certify complete cooking instructions."""
        return service.get_recipes(recipe_ids=recipe_ids, include_dependencies=include_dependencies, cursor=cursor)

    @server.tool()
    def search_knowledge(query: str, books: list[str] | None = None, topics: list[str] | None = None,
                         kinds: list[str] | None = None, recipe_ids: list[str] | None = None,
                         limit: int = 20, cursor: str | None = None) -> dict:
        """Search source passages. Historical cookbook text is not current food-safety guidance; read selected passages and their qualifications."""
        return service.search_knowledge(query=query, books=books, topics=topics, kinds=kinds,
                                        recipe_ids=recipe_ids, limit=limit, cursor=cursor)

    @server.tool()
    def get_passages(passage_ids: list[str], cursor: str | None = None) -> dict:
        """Read cited passages. Follow every continuation with unchanged IDs and preserve source qualifications."""
        return service.get_passages(passage_ids=passage_ids, cursor=cursor)

    return server


def serve(service):
    create_server(service).run(transport="stdio")
