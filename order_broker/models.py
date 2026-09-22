from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OrderStatus(str, Enum):
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Order:
    job_type: str
    payload: Any
    order_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: OrderStatus = OrderStatus.QUEUED
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    producer_id: str | None = None
    worker_id: str | None = None
