"""Visualizer tab: a canvas showing the broker, its connected workers, and
orders animated as markers traveling from the broker out to whichever
worker picked them up, pulsing while in flight. On completion the path
itself flashes green (or red on failure) while the marker fades out.
"""

from __future__ import annotations

import math
import queue
import random
import time
import tkinter as tk
from tkinter import ttk

from .broker_thread import BrokerThread

POLL_MS = 50
TICK_MS = 33
TRAVEL_SECONDS = 0.6
FLASH_SECONDS = 0.5
DISCONNECT_FADE_MS = 1200

BACKGROUND = "#1e2228"
EDGE_COLOR = "#3a3f47"
EDGE_WIDTH = 2
EDGE_HIGHLIGHT_WIDTH = 4
BROKER_COLOR = "#3a6ea5"
IDLE_COLOR = "#9aa5b1"
BUSY_COLOR = "#f0a53d"
DISCONNECTED_COLOR = "#55595f"
SUCCESS_COLOR = "#3fbf6f"
FAIL_COLOR = "#e05a5a"
TEXT_COLOR = "#cfd6de"

# Task marker colors - deliberately excludes green (reserved for the
# success flash), gray (reserved for idle/disconnected nodes), white, and
# black, so a marker never gets confused for broker/node/status feedback.
TASK_COLORS = [
    "#9b59b6",  # purple
    "#e84393",  # pink
    "#f1c40f",  # gold
    "#ff7f50",  # coral
    "#6c5ce7",  # indigo
    "#e17055",  # terracotta
    "#0984e3",  # bright blue
    "#e67e22",  # amber
]

NODE_RADIUS = 22
TASK_RADIUS = 7
WORKER_X = 500
WORKER_Y_START = 70
WORKER_Y_STEP = 90


class _WorkerNode:
    def __init__(self, worker_id: str, queues: list[str], x: float, y: float) -> None:
        self.worker_id = worker_id
        self.queues = queues
        self.x = x
        self.y = y
        self.busy = False
        self.connected = True
        self.oval_id: int | None = None
        self.label_id: int | None = None


class _TaskMarker:
    def __init__(self, order_id: str, start: tuple[float, float], end: tuple[float, float]) -> None:
        self.order_id = order_id
        self.start = start
        self.end = end
        self.spawned_at = time.monotonic()
        self.state = "traveling"  # traveling -> arrived -> completing
        self.completing_started_at: float | None = None
        self.color = random.choice(TASK_COLORS)
        self.canvas_id: int | None = None


class VisualizerTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, broker: BrokerThread) -> None:
        super().__init__(parent, padding=10)
        self.broker = broker
        self.events = broker.add_listener()

        self.workers: dict[str, _WorkerNode] = {}
        self.worker_order: list[str] = []
        self.edges: dict[str, int] = {}
        self.tasks: dict[str, _TaskMarker] = {}
        self.edge_flashes: dict[str, tuple[str, float]] = {}  # worker_id -> (color, started_at)

        self.broker_pos = (110, 300)

        self.canvas = tk.Canvas(self, background=BACKGROUND, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self._draw_broker_node()

        self.after(POLL_MS, self._poll_events)
        self.after(TICK_MS, self._tick)

    # -- static drawing -------------------------------------------------

    def _draw_broker_node(self) -> None:
        x, y = self.broker_pos
        self.canvas.create_oval(
            x - NODE_RADIUS, y - NODE_RADIUS, x + NODE_RADIUS, y + NODE_RADIUS,
            fill=BROKER_COLOR, outline="",
        )
        self.canvas.create_text(x, y, text="Broker", fill="white", font=("TkDefaultFont", 9, "bold"))

    def _worker_pos(self, index: int) -> tuple[float, float]:
        return (WORKER_X, WORKER_Y_START + index * WORKER_Y_STEP)

    def _draw_edge(self, node: _WorkerNode) -> None:
        bx, by = self.broker_pos
        line_id = self.canvas.create_line(bx, by, node.x, node.y, fill=EDGE_COLOR, width=EDGE_WIDTH)
        self.canvas.tag_lower(line_id)
        self.edges[node.worker_id] = line_id

    def _draw_worker(self, node: _WorkerNode) -> None:
        node.oval_id = self.canvas.create_oval(
            node.x - NODE_RADIUS, node.y - NODE_RADIUS, node.x + NODE_RADIUS, node.y + NODE_RADIUS,
            fill=IDLE_COLOR, outline="",
        )
        label = f"{node.worker_id[:6]}\n{', '.join(node.queues)}"
        node.label_id = self.canvas.create_text(
            node.x, node.y + NODE_RADIUS + 16, text=label, fill=TEXT_COLOR,
            font=("TkDefaultFont", 8), justify="center",
        )

    def _redraw_worker(self, node: _WorkerNode) -> None:
        if not node.connected:
            color = DISCONNECTED_COLOR
        else:
            color = BUSY_COLOR if node.busy else IDLE_COLOR
        self.canvas.itemconfig(node.oval_id, fill=color)

    def _remove_worker(self, worker_id: str) -> None:
        node = self.workers.pop(worker_id, None)
        if node is None:
            return
        if node.oval_id is not None:
            self.canvas.delete(node.oval_id)
        if node.label_id is not None:
            self.canvas.delete(node.label_id)
        line_id = self.edges.pop(worker_id, None)
        if line_id is not None:
            self.canvas.delete(line_id)
        if worker_id in self.worker_order:
            self.worker_order.remove(worker_id)

    # -- event handling -------------------------------------------------

    def _poll_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                if kind == "order_event":
                    self._handle_order_event(data)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll_events)

    def _handle_order_event(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "worker_status":
            self._handle_worker_status(msg)
        elif msg_type == "order_update":
            self._handle_order_update(msg)

    def _handle_worker_status(self, msg: dict) -> None:
        worker_id = msg["worker_id"]
        event = msg.get("event")

        if event == "disconnected":
            node = self.workers.get(worker_id)
            if node is not None:
                node.connected = False
                self._redraw_worker(node)
                self.after(DISCONNECT_FADE_MS, lambda: self._remove_worker(worker_id))
            return

        node = self.workers.get(worker_id)
        if node is None:
            index = len(self.worker_order)
            x, y = self._worker_pos(index)
            node = _WorkerNode(worker_id, msg.get("queues", []), x, y)
            self.workers[worker_id] = node
            self.worker_order.append(worker_id)
            self._draw_edge(node)
            self._draw_worker(node)

        node.busy = bool(msg.get("busy"))
        node.queues = msg.get("queues", node.queues)
        self._redraw_worker(node)

    def _handle_order_update(self, msg: dict) -> None:
        order_id = msg["order_id"]
        status = msg.get("status")
        worker_id = msg.get("worker_id")

        if status == "dispatched" and order_id not in self.tasks and worker_id in self.workers:
            worker = self.workers[worker_id]
            marker = _TaskMarker(order_id, self.broker_pos, (worker.x, worker.y))
            marker.canvas_id = self.canvas.create_oval(0, 0, 0, 0, fill=marker.color, outline="")
            self.tasks[order_id] = marker
            return

        if status in ("completed", "failed"):
            marker = self.tasks.get(order_id)
            if marker is not None:
                marker.state = "completing"
                marker.completing_started_at = time.monotonic()
            if worker_id in self.edges:
                color = SUCCESS_COLOR if status == "completed" else FAIL_COLOR
                self.edge_flashes[worker_id] = (color, time.monotonic())

    # -- animation ----------------------------------------------------------

    def _tick(self) -> None:
        now = time.monotonic()
        finished: list[str] = []
        for order_id, marker in self.tasks.items():
            if marker.state == "traveling":
                t = min(1.0, (now - marker.spawned_at) / TRAVEL_SECONDS)
                x = marker.start[0] + (marker.end[0] - marker.start[0]) * t
                y = marker.start[1] + (marker.end[1] - marker.start[1]) * t
                if t >= 1.0:
                    marker.state = "arrived"
                self._place_marker(marker, x, y, TASK_RADIUS)
            elif marker.state == "arrived":
                pulse = 1.0 + 0.25 * math.sin((now - marker.spawned_at) * 4)
                self._place_marker(marker, marker.end[0], marker.end[1], TASK_RADIUS * pulse)
            elif marker.state == "completing":
                elapsed = now - marker.completing_started_at
                shrink = max(0.0, 1.0 - elapsed / FLASH_SECONDS)
                self._place_marker(marker, marker.end[0], marker.end[1], TASK_RADIUS * shrink)
                if elapsed >= FLASH_SECONDS:
                    self.canvas.delete(marker.canvas_id)
                    finished.append(order_id)
        for order_id in finished:
            del self.tasks[order_id]

        expired_edges: list[str] = []
        for worker_id, (color, started_at) in self.edge_flashes.items():
            line_id = self.edges.get(worker_id)
            if line_id is None:
                expired_edges.append(worker_id)
                continue
            elapsed = now - started_at
            if elapsed >= FLASH_SECONDS:
                self.canvas.itemconfig(line_id, fill=EDGE_COLOR, width=EDGE_WIDTH)
                expired_edges.append(worker_id)
            else:
                self.canvas.itemconfig(line_id, fill=color, width=EDGE_HIGHLIGHT_WIDTH)
        for worker_id in expired_edges:
            del self.edge_flashes[worker_id]

        self.after(TICK_MS, self._tick)

    def _place_marker(self, marker: _TaskMarker, x: float, y: float, r: float) -> None:
        self.canvas.coords(marker.canvas_id, x - r, y - r, x + r, y + r)
