import os
import asyncio
from pathlib import Path

# Adjust path context
import sys
sys.path.insert(0, os.path.dirname(__file__))

from kohakuterrarium.bootstrap import load_agent_from_dir

async def main():
    agent_dir = Path(os.path.join(os.path.dirname(__file__), "creatures", "nanobot"))
    agent = load_agent_from_dir(agent_dir)
    print("=== FINAL PROMPT ===")
    prompt = await agent.controller.prompt_manager.build_system_prompt(agent.controller.registry, "bracket")
    print(prompt)

if __name__ == "__main__":
    asyncio.run(main())
