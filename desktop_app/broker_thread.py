"""Bridges the asyncio-based ProducerClient onto a background thread so
Tkinter's synchronous mainloop never has to await anything directly.

The Tkinter side talks to this only through thread-safe calls (connect,
submit, close). Each interested tab calls add_listener() to get its own
queue.Queue and polls it on a timer via root.after(...); events are
fanned out to every listener so multiple tabs can share one connection.
"""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import Any

from order_broker.client import ProducerClient


class BrokerThread:
    def __init__(self) -> None:
        self._listeners: list[queue.Queue] = []
        self._loop = asyncio.new_event_loop()
        self._client: ProducerClient | None = None
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def add_listener(self) -> "queue.Queue[tuple[str, Any]]":
        q: queue.Queue = queue.Queue()
        self._listeners.append(q)
        return q

    def _emit(self, kind: str, data: Any) -> None:
        for q in self._listeners:
            q.put((kind, data))

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _on_event(self, msg: dict) -> None:
        self._emit("order_event", msg)

    def connect(self, uri: str) -> None:
        async def _connect() -> None:
            try:
                client = ProducerClient(uri=uri, on_event=self._on_event)
                await client.connect(subscribe_workers=True)
                self._client = client
                self._emit("connected", uri)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI
                self._emit("connect_error", str(exc))

        asyncio.run_coroutine_threadsafe(_connect(), self._loop)

    def submit(self, job_type: str, payload: Any) -> None:
        if self._client is None:
            self._emit("submit_error", "not connected")
            return

        async def _submit() -> None:
            try:
                await self._client.submit(job_type, payload)
            except Exception as exc:  # noqa: BLE001 - surface any failure to the GUI
                self._emit("submit_error", str(exc))

        asyncio.run_coroutine_threadsafe(_submit(), self._loop)

    def close(self) -> None:
        client = self._client
        if client is not None:
            fut = asyncio.run_coroutine_threadsafe(client.close(), self._loop)
            try:
                fut.result(timeout=2)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
