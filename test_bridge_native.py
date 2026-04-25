import asyncio
import os
import sys
import logging

sys.path.insert(0, os.path.dirname(__file__))

# Enable DEBUG logging to capture proxy or API exceptions natively
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')

from nanobot_kt.bridge import NanobotBridge

async def main():
    bridge = NanobotBridge()
    await bridge.start()
    
    print("\n" + "="*50)
    print("SENDING TEST QUERY to Native Tool Format...")
    print("="*50 + "\n")
    
    query = "请使用 sql_analysis 工具帮我统计总消息数"
    response = await bridge.handle_message(query)
    
    print("\n" + "="*50)
    print("FINAL RESPONSE FROM BRIDGE:")
    print("="*50)
    print(response)

if __name__ == "__main__":
    asyncio.run(main())
