import asyncio

import pytest
import websockets

from order_broker import protocol as proto
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


async def test_order_requeued_when_worker_disconnects_mid_job(broker_uri):
    flaky_ws = await websockets.connect(broker_uri)
    await flaky_ws.send(proto.encode(proto.REGISTER_WORKER, queues=["flaky"]))
    await flaky_ws.recv()  # register_ack

    producer = ProducerClient(uri=broker_uri)
    await producer.connect()
    submit_task = asyncio.create_task(producer.submit("flaky", {"n": 1}))

    dispatch_msg = proto.decode(await asyncio.wait_for(flaky_ws.recv(), timeout=5))
    assert dispatch_msg["type"] == "dispatch"
    await flaky_ws.close()  # vanish mid-job, without ever sending order_result

    reliable = WorkerClient(queues=["flaky"], handler=echo_handler, uri=broker_uri)
    reliable_task = asyncio.create_task(reliable.run())

    try:
        result = await asyncio.wait_for(submit_task, timeout=5)
    finally:
        await producer.close()
        reliable_task.cancel()

    assert result["status"] == "completed"
    assert result["result"] == {"job_type": "flaky", "echo": {"n": 1}}


async def test_worker_status_broadcast_and_order_update_fields(broker_uri):
    admin_ws = await websockets.connect(broker_uri)
    await admin_ws.send(proto.encode(proto.SUBSCRIBE_WORKERS))

    worker = WorkerClient(queues=["echo"], handler=echo_handler, uri=broker_uri)
    worker_task = asyncio.create_task(worker.run())

    connected_msg = proto.decode(await asyncio.wait_for(admin_ws.recv(), timeout=5))
    assert connected_msg["event"] == "connected"
    assert connected_msg["queues"] == ["echo"]
    assert connected_msg["busy"] is False
    worker_id = connected_msg["worker_id"]

    producer = ProducerClient(uri=broker_uri)
    await producer.connect()
    submit_task = asyncio.create_task(producer.submit("echo", {"x": 9}))

    # busy/idle must arrive in this order regardless of when submit() itself
    # resolves, since dispatch always precedes the order's terminal result.
    busy_msg = proto.decode(await asyncio.wait_for(admin_ws.recv(), timeout=5))
    assert busy_msg["event"] == "busy"
    assert busy_msg["worker_id"] == worker_id
    assert busy_msg["busy"] is True

    idle_msg = proto.decode(await asyncio.wait_for(admin_ws.recv(), timeout=5))
    assert idle_msg["event"] == "idle"
    assert idle_msg["worker_id"] == worker_id
    assert idle_msg["busy"] is False

    try:
        result = await asyncio.wait_for(submit_task, timeout=5)
    finally:
        await producer.close()
        worker_task.cancel()
        await admin_ws.close()

    assert result["status"] == "completed"
    assert result["job_type"] == "echo"
    assert result["worker_id"] == worker_id
