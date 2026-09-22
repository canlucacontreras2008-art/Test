"""An example worker program that handles "add" and "shout" orders.

Usage: python examples/example_worker.py
"""

import asyncio
import sys

sys.path.insert(0, ".")
from order_broker.client import WorkerClient


async def handle(job_type: str, payload):
    if job_type == "add":
        return payload["a"] + payload["b"]
    if job_type == "shout":
        return str(payload).upper()
    raise ValueError(f"no handler for job_type {job_type!r}")


async def main():
    client = WorkerClient(queues=["add", "shout"], handler=handle)
    print("worker registered for: add, shout")
    await client.run()


if __name__ == "__main__":
    asyncio.run(main())
