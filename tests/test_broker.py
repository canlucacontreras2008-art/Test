import asyncio

import pytest
import websockets

from order_broker.client import ProducerClient, WorkerClient
from order_broker.server import Broker


@pytest.fixture
async def broker_uri():
    broker = Broker()
    async with websockets.serve(broker.handle_connection, "localhost", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://localhost:{port}"


async def echo_handler(job_type, payload):
    return {"job_type": job_type, "echo": payload}


async def failing_handler(job_type, payload):
    raise RuntimeError("boom")


async def test_order_round_trip(broker_uri):
    worker = WorkerClient(queues=["echo"], handler=echo_handler, uri=broker_uri)
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # let the worker register

    producer = ProducerClient(uri=broker_uri)
    await producer.connect()
    try:
        result = await asyncio.wait_for(producer.submit("echo", {"x": 1}), timeout=5)
    finally:
        await producer.close()
        worker_task.cancel()

    assert result["status"] == "completed"
    assert result["result"] == {"job_type": "echo", "echo": {"x": 1}}


async def test_order_failure_is_reported(broker_uri):
    worker = WorkerClient(queues=["boom"], handler=failing_handler, uri=broker_uri)
    worker_task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)

    producer = ProducerClient(uri=broker_uri)
    await producer.connect()
    try:
        result = await asyncio.wait_for(producer.submit("boom", None), timeout=5)
    finally:
        await producer.close()
        worker_task.cancel()

    assert result["status"] == "failed"
    assert result["error"] == "boom"


async def test_order_queued_until_worker_available(broker_uri):
    producer = ProducerClient(uri=broker_uri)
    await producer.connect()
    submit_task = asyncio.create_task(producer.submit("echo", {"late": True}))
    await asyncio.sleep(0.1)  # order should be queued with no worker yet

    worker = WorkerClient(queues=["echo"], handler=echo_handler, uri=broker_uri)
    worker_task = asyncio.create_task(worker.run())

    try:
        result = await asyncio.wait_for(submit_task, timeout=5)
    finally:
        await producer.close()
        worker_task.cancel()

    assert result["status"] == "completed"
    assert result["result"] == {"job_type": "echo", "echo": {"late": True}}
