import asyncio
from nanobot_kt.bridge import NanobotBridge

async def main():
    b = NanobotBridge("creatures/nanobot")
    await b.start()
    tools = b.agent.registry.list_tools()
    print("TOOL_COUNT", len(tools))
    print("TOOLS", tools)
    await b.stop()

asyncio.run(main())
