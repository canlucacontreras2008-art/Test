from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict, deque

import websockets

from . import protocol as p
from .models import Order, OrderStatus

logger = logging.getLogger("order_broker")


class _Worker:
    def __init__(self, worker_id: str, ws, queues: list[str]):
        self.worker_id = worker_id
        self.ws = ws
        self.queues = queues
        self.busy = False


class Broker:
    """In-memory message/order broker.

    Producers submit orders over a persistent WebSocket connection and get
    pushed status updates as they happen. Workers register the job types
    they handle and receive dispatches as orders become available.
    """

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._producer_ws: dict[str, object] = {}
        self._workers: dict[str, _Worker] = {}
        self._pending: dict[str, deque[str]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def handle_connection(self, ws) -> None:
        client_id = uuid.uuid4().hex
        worker: _Worker | None = None
        try:
            async for raw in ws:
                try:
                    msg = p.decode(raw)
                except ValueError as exc:
                    await ws.send(p.encode(p.ERROR, message=str(exc)))
                    continue

                msg_type = msg.get("type")

                if msg_type == p.REGISTER_WORKER:
                    worker = await self._on_register_worker(client_id, ws, msg)
                elif msg_type == p.SUBMIT_ORDER:
                    await self._on_submit_order(client_id, ws, msg)
                elif msg_type == p.ORDER_RESULT:
                    await self._on_order_result(worker, msg)
                elif msg_type == p.PING:
                    await ws.send(p.encode(p.PONG))
                else:
                    await ws.send(p.encode(p.ERROR, message=f"unknown message type {msg_type!r}"))

        except websockets.ConnectionClosed:
            pass
        finally:
            await self._on_disconnect(client_id, worker)

    async def _on_register_worker(self, client_id: str, ws, msg: dict) -> _Worker | None:
        queues = msg.get("queues") or []
        if not isinstance(queues, list) or not queues:
            await ws.send(p.encode(p.ERROR, message="register_worker requires non-empty 'queues'"))
            return None
        worker = _Worker(client_id, ws, queues)
        async with self._lock:
            self._workers[client_id] = worker
        await ws.send(p.encode(p.REGISTER_ACK, worker_id=client_id, queues=queues))
        await self._dispatch_loop()
        return worker

    async def _on_submit_order(self, client_id: str, ws, msg: dict) -> None:
        job_type = msg.get("job_type")
        if not job_type:
            await ws.send(p.encode(p.ERROR, message="submit_order requires 'job_type'"))
            return
        order = Order(job_type=job_type, payload=msg.get("payload"), producer_id=client_id)
        async with self._lock:
            self._orders[order.order_id] = order
            self._producer_ws[client_id] = ws
            self._pending[job_type].append(order.order_id)
        await ws.send(p.encode(p.ORDER_ACCEPTED, order_id=order.order_id, status=order.status.value))
        await self._dispatch_loop()

    async def _on_order_result(self, worker: _Worker | None, msg: dict) -> None:
        order_id = msg.get("order_id")
        order = self._orders.get(order_id)
        if order is None:
            if worker is not None:
                await worker.ws.send(p.encode(p.ERROR, message=f"unknown order_id {order_id!r}"))
            return
        status = msg.get("status")
        async with self._lock:
            order.status = OrderStatus.COMPLETED if status == "completed" else OrderStatus.FAILED
            order.result = msg.get("result")
            order.error = msg.get("error")
            if worker is not None:
                worker.busy = False
        await self._notify_producer(order)
        await self._dispatch_loop()

    async def _on_disconnect(self, client_id: str, worker: _Worker | None) -> None:
        async with self._lock:
            self._producer_ws.pop(client_id, None)
            if worker is not None:
                self._workers.pop(client_id, None)
                if worker.busy:
                    for order in self._orders.values():
                        if order.worker_id == worker.worker_id and order.status == OrderStatus.DISPATCHED:
                            order.status = OrderStatus.QUEUED
                            order.worker_id = None
                            self._pending[order.job_type].append(order.order_id)
        await self._dispatch_loop()

    async def _notify_producer(self, order: Order) -> None:
        ws = self._producer_ws.get(order.producer_id)
        if ws is None:
            return
        try:
            await ws.send(p.encode(
                p.ORDER_UPDATE,
                order_id=order.order_id,
                status=order.status.value,
                result=order.result,
                error=order.error,
            ))
        except websockets.ConnectionClosed:
            pass

    async def _dispatch_loop(self) -> None:
        async with self._lock:
            for job_type, queue in list(self._pending.items()):
                available = [w for w in self._workers.values() if not w.busy and job_type in w.queues]
                while queue and available:
                    worker = available.pop(0)
                    order_id = queue.popleft()
                    order = self._orders[order_id]
                    order.status = OrderStatus.DISPATCHED
                    order.worker_id = worker.worker_id
                    worker.busy = True
                    try:
                        await worker.ws.send(p.encode(
                            p.DISPATCH,
                            order_id=order.order_id,
                            job_type=order.job_type,
                            payload=order.payload,
                        ))
                    except websockets.ConnectionClosed:
                        order.status = OrderStatus.QUEUED
                        order.worker_id = None
                        worker.busy = False
                        queue.appendleft(order_id)
                        continue
                    await self._notify_producer(order)


async def run_server(host: str = "localhost", port: int = 8765) -> None:
    broker = Broker()
    async with websockets.serve(broker.handle_connection, host, port):
        logger.info("order broker listening on ws://%s:%s", host, port)
        await asyncio.Future()
