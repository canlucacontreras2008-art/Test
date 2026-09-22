"""An example producer program that submits orders and prints the results.

Usage: python examples/example_producer.py
"""

import asyncio
import sys

sys.path.insert(0, ".")
from order_broker.client import ProducerClient


async def main():
    client = ProducerClient()
    await client.connect()
    try:
        result = await client.submit("add", {"a": 2, "b": 3})
        print("add result:", result)

        result = await client.submit("shout", "hello there")
        print("shout result:", result)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
