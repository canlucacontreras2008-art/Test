"""Start the order broker.

Usage: python examples/run_server.py [host] [port]
"""

import asyncio
import logging
import sys

sys.path.insert(0, ".")
from order_broker import run_server

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    host = sys.argv[1] if len(sys.argv) > 1 else "localhost"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8765
    asyncio.run(run_server(host, port))
