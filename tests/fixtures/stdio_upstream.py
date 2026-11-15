"""A stdio MCP upstream for tests/test_hub.py. Tool `grow` adds a tool and sends tools/list_changed;
`crash` ends the process, as a server that dies would."""
import os

from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("stdio-fixture")


@mcp.tool()
def echo(text: str) -> str:
    """Echo the text back."""
    return f"stdio:{text}"


@mcp.tool()
async def grow(name: str, ctx: Context) -> str:
    """Add a tool called `name`, and tell the client the list changed."""
    mcp.add_tool(lambda: f"stdio:{name}", name=name, description=f"Grown tool {name}")
    await ctx.session.send_tool_list_changed()
    return "grown"


@mcp.tool()
def crash() -> str:
    """Exit the process."""
    os._exit(1)


if __name__ == "__main__":
    mcp.run("stdio")
