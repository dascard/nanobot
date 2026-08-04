"""仅供 MCP 客户端集成测试启动的 stdio server。"""

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("nanobot-mcp-test")


@mcp.tool()
def echo(value: str) -> dict[str, str]:
    """回显测试文本。"""

    return {"value": value}


if __name__ == "__main__":
    mcp.run(transport="stdio")
