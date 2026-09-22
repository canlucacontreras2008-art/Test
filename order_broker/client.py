"""Reusable async clients for connecting other programs to the broker."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable

import websockets

from . import protocol as p


class ProducerClient:
    """Submits orders to the broker and receives status updates for them.

    All reads happen on a single background listener task; submit() just
    queues futures for that task to resolve, so concurrent submits on the
    same client stay correlated with the responses the broker sends back
    in request order.
    """

    def __init__(self, uri: str = "ws://localhost:8765") -> None:
        self.uri = uri
        self._ws = None
        self._accept_waiters: deque[asyncio.Future] = deque()
        self._result_waiters: dict[str, asyncio.Future] = {}
        self._listener_task: asyncio.Task | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self.uri)
        self._listener_task = asyncio.create_task(self._listen())

    async def close(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
        if self._ws is not None:
            await self._ws.close()

    async def submit(self, job_type: str, payload: Any) -> dict:
        """Submit an order and wait for its terminal result (completed/failed)."""
        accepted: asyncio.Future = asyncio.get_event_loop().create_future()
        self._accept_waiters.append(accepted)
        await self._ws.send(p.encode(p.SUBMIT_ORDER, job_type=job_type, payload=payload))
        order_id = await accepted
        result: asyncio.Future = asyncio.get_event_loop().create_future()
        self._result_waiters[order_id] = result
        return await result

    async def _listen(self) -> None:
        async for raw in self._ws:
            msg = p.decode(raw)
            msg_type = msg.get("type")
            if msg_type == p.ORDER_ACCEPTED:
                if self._accept_waiters:
                    self._accept_waiters.popleft().set_result(msg["order_id"])
            elif msg_type == p.ORDER_UPDATE:
                order_id = msg["order_id"]
                if msg["status"] in ("completed", "failed") and order_id in self._result_waiters:
                    self._result_waiters.pop(order_id).set_result(msg)


class WorkerClient:
    """Registers for job types and runs a handler for each dispatched order."""

    def __init__(
        self,
        queues: list[str],
        handler: Callable[[str, Any], Awaitable[Any]],
        uri: str = "ws://localhost:8765",
    ) -> None:
        self.queues = queues
        self.handler = handler
        self.uri = uri
        self._ws = None

    async def run(self) -> None:
        async with websockets.connect(self.uri) as ws:
            self._ws = ws
            await ws.send(p.encode(p.REGISTER_WORKER, queues=self.queues))
            await ws.recv()  # register_ack
            async for raw in ws:
                msg = p.decode(raw)
                if msg.get("type") != p.DISPATCH:
                    continue
                await self._handle_dispatch(ws, msg)

    async def _handle_dispatch(self, ws, msg: dict) -> None:
        order_id = msg["order_id"]
        try:
            result = await self.handler(msg["job_type"], msg["payload"])
            await ws.send(p.encode(p.ORDER_RESULT, order_id=order_id, status="completed", result=result))
        except Exception as exc:  # noqa: BLE001 - report worker failures back to the broker
            await ws.send(p.encode(p.ORDER_RESULT, order_id=order_id, status="failed", error=str(exc)))
