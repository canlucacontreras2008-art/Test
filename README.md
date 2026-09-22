# order-broker

A small WebSocket broker for receiving and dispatching messages/orders
between other programs. Programs hold a persistent connection to the
broker and play one of two roles:

- **Producers** submit orders (`job_type` + `payload`) and get pushed
  status updates (`queued` → `dispatched` → `completed`/`failed`) as they
  happen.
- **Workers** register the job types they handle and receive orders as
  they're dispatched, then report the result back.

The broker matches queued orders to available workers, keeps orders
queued if no worker is free yet, and requeues an order if the worker
handling it disconnects mid-job.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Run it

In separate terminals:

```bash
.venv/bin/python examples/run_server.py            # broker on ws://localhost:8765
.venv/bin/python examples/example_worker.py         # handles "add" and "shout" orders
.venv/bin/python examples/example_producer.py        # submits orders, prints results
```

## Writing your own producer or worker

```python
from order_broker.client import ProducerClient, WorkerClient

# Producer
client = ProducerClient(uri="ws://localhost:8765")
await client.connect()
result = await client.submit("add", {"a": 2, "b": 3})
await client.close()

# Worker
async def handle(job_type, payload):
    if job_type == "add":
        return payload["a"] + payload["b"]
    raise ValueError(f"unhandled job_type {job_type}")

worker = WorkerClient(queues=["add"], handler=handle, uri="ws://localhost:8765")
await worker.run()
```

Any program in any language can act as a producer or worker too — the
wire protocol is plain JSON over WebSocket, documented in
`order_broker/protocol.py`.

## Desktop app

`desktop_app/` is a Tkinter GUI with two tabs:

- **Builder** — design order forms: give a `job_type` a name and a list of
  typed fields (`str`/`int`/`float`/`bool`). Forms are saved as JSON under
  `~/.order_broker/forms/`.
- **Dashboard** — connect to a broker, fill in a saved form (or submit a
  raw `job_type` + JSON payload) and watch a live table of orders as they
  move through `queued` → `dispatched` → `completed`/`failed`.

It needs a Tk-enabled Python. On Debian/Ubuntu, install the binding for
whichever Python runs it (match the version, e.g. `python3.11-tk` for
Python 3.11):

```bash
sudo apt install python3-tk   # or python3.11-tk / python3.12-tk, etc.
```

Then, with the broker running (see above):

```bash
.venv/bin/python -m desktop_app
```

The first run seeds two example forms (`add`, `shout`) that work with
`examples/example_worker.py`.

## Wire protocol

Every message is one JSON object with a `"type"` field.

| Direction         | type              | fields                                    |
|--------------------|-------------------|--------------------------------------------|
| producer -> broker | `submit_order`    | `job_type`, `payload`                       |
| broker -> producer  | `order_accepted`  | `order_id`, `status`                        |
| broker -> producer  | `order_update`    | `order_id`, `status`, `result?`, `error?`   |
| worker -> broker   | `register_worker` | `queues` (list of job types)                |
| broker -> worker    | `register_ack`    | `worker_id`, `queues`                       |
| broker -> worker    | `dispatch`        | `order_id`, `job_type`, `payload`           |
| worker -> broker   | `order_result`    | `order_id`, `status`, `result?`, `error?`   |
| either -> broker    | `ping`            | —                                            |
| broker -> either    | `pong` / `error`  | `message?`                                   |

## Tests

```bash
.venv/bin/python -m pytest tests/ -v
```

## Notes / limitations

- Orders and workers are held in memory only — nothing persists across a
  broker restart.
- If a producer disconnects, its in-flight orders still run to
  completion, but there's no one left to notify; reconnect and resubmit
  if you need the result.
- One process per role isn't required — a single connection could send
  both `register_worker` and `submit_order` if you want a program to be
  both producer and worker.
