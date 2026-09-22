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

`desktop_app/` is a Tkinter GUI with three tabs:

- **Dashboard** — connect to a broker, fill in a saved form (or submit a
  raw `job_type` + JSON payload) and watch a live table of orders as they
  move through `queued` → `dispatched` → `completed`/`failed`.
- **Visualizer** — a live canvas view of the broker and its workers: each
  connected worker is a node (grey = idle, orange = busy, fading out on
  disconnect) joined to the broker by a path, and every dispatched order
  animates as a marker traveling that path, pulsing while the worker is
  on it and flashing green/red when it completes or fails.
- **Builder** — design order forms: give a `job_type` a name and a list of
  typed fields (`str`/`int`/`float`/`bool`). Forms are saved as JSON under
  `~/.order_broker/forms/`.

The Dashboard and Visualizer share one connection to the broker (opted
into worker-status broadcasts via `subscribe_workers`), so anything
submitted from the Dashboard shows up moving through the Visualizer too.

It needs a Tk-enabled Python. On Debian/Ubuntu, install the binding for
whichever Python runs it (match the version, e.g. `python3.11-tk` for
Python 3.11, `python3.12-tk` for 3.12):

```bash
sudo apt install python3-tk   # or python3.11-tk / python3.12-tk, etc.
```

If your distro only ships `-tk` bindings for its default Python version
(commonly the case for non-default versions installed via a PPA), it's
simplest to build the venv with that default version instead:

```bash
sudo apt install python3-tk
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

Then, with the broker running (see above):

```bash
.venv/bin/python -m desktop_app
```

The first run seeds two example forms (`add`, `shout`) that work with
`examples/example_worker.py`.

## Web UI (phone-friendly)

`web/` is a mobile-friendly static page — same Dashboard + Visualizer
features as the desktop app's first two tabs, but it's plain HTML/CSS/
vanilla JS with no build step, and it talks to the broker directly from
the browser over the same WebSocket protocol. Anything reachable with a
browser (including a phone) can use it.

```bash
.venv/bin/python examples/run_web_ui.py localhost 8080
```

Then open `http://localhost:8080/` — the page defaults its broker URI to
`ws://<the page's own host>:8765`, so if you serve both on the same
machine you can just hit Connect. On a phone, edit that field to point at
wherever the broker actually runs.

## Accessing it from your phone (Raspberry Pi + Tailscale)

To check in on the broker from your phone from anywhere (not just your
home Wi-Fi), run everything on a small always-on machine like a
Raspberry Pi, and use [Tailscale](https://tailscale.com/) to reach it —
a free private network between your own devices. It avoids the usual
self-hosting headaches (port forwarding, dynamic DNS, exposing an
unauthenticated service to the public internet) since only devices
logged into your Tailscale account can reach the Pi at all.

**On the Pi:**

```bash
# 1. Get the code and dependencies onto the Pi (git clone, scp, etc.),
#    then from the project directory:
sudo apt install python3-venv python3-tk
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# 2. Bind the broker and web UI to all interfaces, not just localhost,
#    so something other than the Pi itself can reach them:
.venv/bin/python examples/run_server.py 0.0.0.0 8765 &
.venv/bin/python examples/run_web_ui.py 0.0.0.0 8080 &
.venv/bin/python examples/example_worker.py &   # or your own workers

# 3. Install Tailscale and join your account:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

`tailscale up` prints (and `tailscale ip -4` shows anytime after) the
Pi's Tailscale address, e.g. `100.x.x.x`, or a stable MagicDNS name like
`raspberrypi.your-tailnet.ts.net`.

**On your phone:** install the Tailscale app, log into the same account,
then open `http://<that address>:8080/` in your phone's browser and set
the broker URI field to `ws://<that address>:8765`.

To keep the broker/web UI/workers running across reboots instead of
babysitting `&` background jobs over SSH, wrap each command in a systemd
unit (`ExecStart=/path/to/.venv/bin/python examples/run_server.py 0.0.0.0
8765`, `WantedBy=multi-user.target`, etc.) and `systemctl enable` them.

**Security note:** the broker has no authentication - anyone who can
reach it (i.e. anyone on your tailnet) can submit orders and see all
order data. Fine for a personal single-user tailnet; if you ever share
that tailnet with other people, or expose these ports outside Tailscale
entirely, add auth in front of it first.

## Wire protocol

Every message is one JSON object with a `"type"` field.

| Direction            | type                | fields                                                             |
|-----------------------|---------------------|---------------------------------------------------------------------|
| producer -> broker    | `submit_order`      | `job_type`, `payload`                                                |
| broker -> producer    | `order_accepted`    | `order_id`, `status`                                                 |
| broker -> producer    | `order_update`      | `order_id`, `status`, `job_type`, `worker_id?`, `result?`, `error?`  |
| worker -> broker      | `register_worker`   | `queues` (list of job types)                                         |
| broker -> worker      | `register_ack`      | `worker_id`, `queues`                                                |
| broker -> worker      | `dispatch`          | `order_id`, `job_type`, `payload`                                    |
| worker -> broker      | `order_result`      | `order_id`, `status`, `result?`, `error?`                            |
| either -> broker      | `ping`              | —                                                                     |
| either -> broker      | `subscribe_workers` | — opt in to `worker_status` broadcasts                               |
| broker -> subscriber  | `worker_status`     | `worker_id`, `queues`, `busy`, `current_order_id`, `event`           |
| broker -> either      | `pong` / `error`    | `message?`                                                           |

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
