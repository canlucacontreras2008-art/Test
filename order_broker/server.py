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
        self.current_order_id: str | None = None


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
        self._worker_subscribers: dict[str, object] = {}
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
                elif msg_type == p.SUBSCRIBE_WORKERS:
                    await self._on_subscribe_workers(client_id, ws)
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
        await self._broadcast_worker_status(worker, "connected")
        await self._dispatch_loop(queues)
        return worker

    async def _on_subscribe_workers(self, client_id: str, ws) -> None:
        async with self._lock:
            self._worker_subscribers[client_id] = ws
            snapshot = list(self._workers.values())
        for worker in snapshot:
            await ws.send(p.encode(
                p.WORKER_STATUS,
                worker_id=worker.worker_id,
                queues=worker.queues,
                busy=worker.busy,
                current_order_id=worker.current_order_id,
                event="busy" if worker.busy else "connected",
            ))

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
        await self._dispatch_loop([job_type])

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
                worker.current_order_id = None
        await self._notify_producer(order)
        if worker is not None:
            await self._broadcast_worker_status(worker, "idle")
            # Only this worker just freed up, so only its own queues can
            # possibly have new work for it - no need to rescan every job type.
            await self._dispatch_loop(worker.queues)

    async def _on_disconnect(self, client_id: str, worker: _Worker | None) -> None:
        # Note: no dispatch_loop call here - losing a connection never frees
        # up new capacity, so there's nothing new to dispatch.
        async with self._lock:
            self._producer_ws.pop(client_id, None)
            self._worker_subscribers.pop(client_id, None)
            if worker is not None:
                self._workers.pop(client_id, None)
                if worker.busy and worker.current_order_id is not None:
                    order = self._orders.get(worker.current_order_id)
                    if order is not None and order.status == OrderStatus.DISPATCHED:
                        order.status = OrderStatus.QUEUED
                        order.worker_id = None
                        self._pending[order.job_type].append(order.order_id)
        if worker is not None:
            await self._broadcast_worker_status(worker, "disconnected")

    async def _notify_producer(self, order: Order) -> None:
        ws = self._producer_ws.get(order.producer_id)
        if ws is None:
            return
        try:
            await ws.send(p.encode(
                p.ORDER_UPDATE,
                order_id=order.order_id,
                status=order.status.value,
                job_type=order.job_type,
                worker_id=order.worker_id,
                result=order.result,
                error=order.error,
            ))
        except websockets.ConnectionClosed:
            pass

    async def _broadcast_worker_status(self, worker: _Worker, event: str) -> None:
        msg = p.encode(
            p.WORKER_STATUS,
            worker_id=worker.worker_id,
            queues=worker.queues,
            busy=worker.busy,
            current_order_id=worker.current_order_id,
            event=event,
        )
        for ws in list(self._worker_subscribers.values()):
            try:
                await ws.send(msg)
            except websockets.ConnectionClosed:
                pass

    async def _dispatch_loop(self, job_types: list[str] | None = None) -> None:
        """Try to match queued orders to free workers.

        `job_types` scopes the attempt to just those queues - the caller
        knows which queues could possibly have gained new orders or new
        capacity since the last dispatch, so most callers only need to
        check a handful of queues rather than every one that has ever had
        an order (this matters once a broker has been up long enough to
        have accumulated many distinct job types).
        """
        async with self._lock:
            types_to_check = job_types if job_types is not None else list(self._pending.keys())
            for job_type in types_to_check:
                queue = self._pending.get(job_type)
                if not queue:
                    continue
                available = [w for w in self._workers.values() if not w.busy and job_type in w.queues]
                while queue and available:
                    worker = available.pop(0)
                    order_id = queue.popleft()
                    order = self._orders[order_id]
                    order.status = OrderStatus.DISPATCHED
                    order.worker_id = worker.worker_id
                    worker.busy = True
                    worker.current_order_id = order.order_id
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
                        worker.current_order_id = None
                        queue.appendleft(order_id)
                        continue
                    await self._broadcast_worker_status(worker, "busy")
                    await self._notify_producer(order)


async def run_server(host: str = "localhost", port: int = 8765) -> None:
    broker = Broker()
    async with websockets.serve(broker.handle_connection, host, port):
        logger.info("order broker listening on ws://%s:%s", host, port)
        await asyncio.Future()
