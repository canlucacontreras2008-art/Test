"""JSON message protocol shared by the broker and its clients.

Every message is a single JSON object with a "type" field. Messages flow
over a persistent WebSocket connection in either direction:

Producer -> broker:
    submit_order   {type, job_type, payload}
Worker -> broker:
    register_worker {type, queues: [job_type, ...]}
    order_result     {type, order_id, status: "completed"|"failed", result?, error?}
Either -> broker:
    ping             {type}
    subscribe_workers {type}  -- opt in to worker_status broadcasts

Broker -> producer:
    order_accepted {type, order_id, status}
    order_update   {type, order_id, status, job_type, worker_id?, result?, error?}
Broker -> worker:
    register_ack   {type, worker_id, queues}
    dispatch       {type, order_id, job_type, payload}
Broker -> subscriber:
    worker_status  {type, worker_id, queues, busy, current_order_id, event: "connected"|"idle"|"busy"|"disconnected"}
Broker -> either:
    pong           {type}
    error          {type, message}
"""

from __future__ import annotations

import json
from typing import Any

SUBMIT_ORDER = "submit_order"
REGISTER_WORKER = "register_worker"
ORDER_RESULT = "order_result"
PING = "ping"
SUBSCRIBE_WORKERS = "subscribe_workers"

REGISTER_ACK = "register_ack"
ORDER_ACCEPTED = "order_accepted"
DISPATCH = "dispatch"
ORDER_UPDATE = "order_update"
WORKER_STATUS = "worker_status"
PONG = "pong"
ERROR = "error"


def encode(msg_type: str, **fields: Any) -> str:
    return json.dumps({"type": msg_type, **fields})


def decode(raw: str) -> dict:
    msg = json.loads(raw)
    if not isinstance(msg, dict) or "type" not in msg:
        raise ValueError("message must be a JSON object with a 'type' field")
    return msg
