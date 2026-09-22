(() => {
  "use strict";

  // -- protocol constants (mirrors order_broker/protocol.py) --------------
  const SUBMIT_ORDER = "submit_order";
  const SUBSCRIBE_WORKERS = "subscribe_workers";
  const ORDER_ACCEPTED = "order_accepted";
  const ORDER_UPDATE = "order_update";
  const WORKER_STATUS = "worker_status";
  const ERROR = "error";

  // -- dom -----------------------------------------------------------------
  const uriInput = document.getElementById("uri");
  const connectBtn = document.getElementById("connectBtn");
  const statusEl = document.getElementById("status");
  const jobTypeInput = document.getElementById("jobType");
  const payloadInput = document.getElementById("payload");
  const submitBtn = document.getElementById("submitBtn");
  const ordersBody = document.querySelector("#ordersTable tbody");
  const canvas = document.getElementById("canvas");
  const ctx = canvas.getContext("2d");

  uriInput.value = `ws://${location.hostname || "localhost"}:8765`;

  // -- tabs ------------------------------------------------------------------
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById(btn.dataset.tab).classList.add("active");
      resizeCanvas();
    });
  });

  // -- connection ------------------------------------------------------------
  let ws = null;
  const pendingAccepts = []; // rows awaiting an order_accepted, in submit order

  function setStatus(text, cls) {
    statusEl.textContent = text;
    statusEl.className = "status" + (cls ? ` ${cls}` : "");
  }

  connectBtn.addEventListener("click", () => {
    if (ws) {
      ws.close();
      ws = null;
    }
    setStatus("connecting...");
    try {
      ws = new WebSocket(uriInput.value.trim());
    } catch (err) {
      setStatus(`invalid URI: ${err.message}`, "error");
      return;
    }
    ws.addEventListener("open", () => {
      setStatus(`connected to ${uriInput.value.trim()}`, "connected");
      ws.send(JSON.stringify({ type: SUBSCRIBE_WORKERS }));
    });
    ws.addEventListener("close", () => setStatus("disconnected", "error"));
    ws.addEventListener("error", () => setStatus("connection error", "error"));
    ws.addEventListener("message", (event) => {
      let msg;
      try {
        msg = JSON.parse(event.data);
      } catch {
        return;
      }
      handleMessage(msg);
    });
  });

  submitBtn.addEventListener("click", () => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      alert("Not connected.");
      return;
    }
    const jobType = jobTypeInput.value.trim();
    if (!jobType) {
      alert("job_type is required.");
      return;
    }
    let payload = null;
    const raw = payloadInput.value.trim();
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (err) {
        alert(`Invalid JSON payload: ${err.message}`);
        return;
      }
    }
    const row = insertOrderRow({ order_id: "(pending)", job_type: jobType, status: "submitting", result: "", error: "" });
    pendingAccepts.push(row);
    ws.send(JSON.stringify({ type: SUBMIT_ORDER, job_type: jobType, payload }));
  });

  function handleMessage(msg) {
    if (msg.type === ORDER_ACCEPTED) {
      const row = pendingAccepts.shift();
      if (row) {
        row.dataset.orderId = msg.order_id;
        row.querySelector(".c-order-id").textContent = msg.order_id;
        row.querySelector(".c-status").textContent = msg.status;
        ordersByRowId.set(msg.order_id, row);
      }
    } else if (msg.type === ORDER_UPDATE) {
      const row = ordersByRowId.get(msg.order_id);
      if (row) {
        row.querySelector(".c-status").textContent = msg.status;
        row.querySelector(".c-result").textContent = msg.result == null ? "" : JSON.stringify(msg.result);
        row.querySelector(".c-error").textContent = msg.error || "";
      }
      handleOrderUpdateForVisualizer(msg);
    } else if (msg.type === WORKER_STATUS) {
      handleWorkerStatus(msg);
    } else if (msg.type === ERROR) {
      console.error("broker error:", msg.message);
    }
  }

  // -- orders table ------------------------------------------------------------
  const ordersByRowId = new Map(); // order_id -> row element

  function insertOrderRow({ order_id, job_type, status, result, error }) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td class="c-order-id">${order_id}</td>
      <td class="c-job-type">${job_type}</td>
      <td class="c-status">${status}</td>
      <td class="c-result">${result}</td>
      <td class="c-error">${error}</td>
    `;
    ordersBody.prepend(row);
    return row;
  }

  // -- visualizer ------------------------------------------------------------
  const BROKER_COLOR = "#3a6ea5";
  const IDLE_COLOR = "#9aa5b1";
  const BUSY_COLOR = "#f0a53d";
  const DISCONNECTED_COLOR = "#55595f";
  const SUCCESS_COLOR = "#3fbf6f";
  const FAIL_COLOR = "#e05a5a";
  const EDGE_COLOR = "#3a3f47";
  const TEXT_COLOR = "#cfd6de";
  const EDGE_WIDTH = 2;
  const EDGE_HIGHLIGHT_WIDTH = 4;

  // Deliberately excludes green (success flash), gray (idle/disconnected),
  // white, and black, so a task marker never reads as status feedback.
  const TASK_COLORS = [
    "#9b59b6", "#e84393", "#f1c40f", "#ff7f50",
    "#6c5ce7", "#e17055", "#0984e3", "#e67e22",
  ];

  const NODE_RADIUS = 20;
  const TASK_RADIUS = 6;
  const TRAVEL_SECONDS = 0.6;
  const FLASH_SECONDS = 0.5;
  const DISCONNECT_FADE_MS = 1200;

  const workers = new Map(); // worker_id -> {queues, x, y, busy, connected}
  const workerOrder = [];
  const tasks = new Map(); // order_id -> marker
  const edgeFlashes = new Map(); // worker_id -> {color, startedAt}

  let brokerPos = { x: 70, y: 200 };

  function resizeCanvas() {
    const rect = canvas.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    brokerPos = { x: 60, y: rect.height / 2 };
    layoutWorkers(rect);
  }

  function layoutWorkers(rect) {
    const x = Math.max(rect.width - 90, brokerPos.x + 140);
    const step = 90;
    workerOrder.forEach((id, i) => {
      const w = workers.get(id);
      if (!w) return;
      w.x = x;
      w.y = 60 + i * step;
    });
  }

  window.addEventListener("resize", resizeCanvas);
  resizeCanvas();

  function handleWorkerStatus(msg) {
    const { worker_id: id, event } = msg;

    if (event === "disconnected") {
      const w = workers.get(id);
      if (w) {
        w.connected = false;
        setTimeout(() => {
          workers.delete(id);
          const idx = workerOrder.indexOf(id);
          if (idx >= 0) workerOrder.splice(idx, 1);
          resizeCanvas();
        }, DISCONNECT_FADE_MS);
      }
      return;
    }

    let w = workers.get(id);
    if (!w) {
      w = { id, queues: msg.queues || [], x: 0, y: 0, busy: false, connected: true };
      workers.set(id, w);
      workerOrder.push(id);
      resizeCanvas();
    }
    w.busy = !!msg.busy;
    w.queues = msg.queues || w.queues;
  }

  function handleOrderUpdateForVisualizer(msg) {
    const { order_id: orderId, status, worker_id: workerId } = msg;

    if (status === "dispatched" && !tasks.has(orderId) && workers.has(workerId)) {
      const w = workers.get(workerId);
      tasks.set(orderId, {
        state: "traveling",
        spawnedAt: performance.now(),
        completingStartedAt: null,
        start: { x: brokerPos.x, y: brokerPos.y },
        end: { x: w.x, y: w.y },
        workerId,
        color: TASK_COLORS[Math.floor(Math.random() * TASK_COLORS.length)],
        radius: TASK_RADIUS,
      });
      return;
    }

    if (status === "completed" || status === "failed") {
      const marker = tasks.get(orderId);
      if (marker) {
        marker.state = "completing";
        marker.completingStartedAt = performance.now();
      }
      if (workers.has(workerId)) {
        edgeFlashes.set(workerId, {
          color: status === "completed" ? SUCCESS_COLOR : FAIL_COLOR,
          startedAt: performance.now(),
        });
      }
    }
  }

  function drawNode(x, y, r, fill, label) {
    ctx.beginPath();
    ctx.arc(x, y, r, 0, Math.PI * 2);
    ctx.fillStyle = fill;
    ctx.fill();
    if (label) {
      ctx.fillStyle = "white";
      ctx.font = "bold 11px -apple-system, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(label, x, y);
    }
  }

  function drawLabel(x, y, text) {
    ctx.fillStyle = TEXT_COLOR;
    ctx.font = "10px -apple-system, sans-serif";
    ctx.textAlign = "center";
    text.split("\n").forEach((line, i) => ctx.fillText(line, x, y + i * 12));
  }

  function render(now) {
    const rect = canvas.getBoundingClientRect();
    ctx.clearRect(0, 0, rect.width, rect.height);

    // edges (drawn first, under nodes)
    for (const id of workerOrder) {
      const w = workers.get(id);
      if (!w) continue;
      const flash = edgeFlashes.get(id);
      let color = EDGE_COLOR;
      let width = EDGE_WIDTH;
      if (flash) {
        const elapsed = (now - flash.startedAt) / 1000;
        if (elapsed >= FLASH_SECONDS) {
          edgeFlashes.delete(id);
        } else {
          color = flash.color;
          width = EDGE_HIGHLIGHT_WIDTH;
        }
      }
      ctx.beginPath();
      ctx.moveTo(brokerPos.x, brokerPos.y);
      ctx.lineTo(w.x, w.y);
      ctx.strokeStyle = color;
      ctx.lineWidth = width;
      ctx.stroke();
    }

    // task markers
    for (const [orderId, marker] of tasks) {
      let x, y, r;
      if (marker.state === "traveling") {
        const t = Math.min(1, (now - marker.spawnedAt) / 1000 / TRAVEL_SECONDS);
        x = marker.start.x + (marker.end.x - marker.start.x) * t;
        y = marker.start.y + (marker.end.y - marker.start.y) * t;
        r = TASK_RADIUS;
        if (t >= 1) marker.state = "arrived";
      } else if (marker.state === "arrived") {
        const pulse = 1 + 0.25 * Math.sin(((now - marker.spawnedAt) / 1000) * 4);
        x = marker.end.x;
        y = marker.end.y;
        r = TASK_RADIUS * pulse;
      } else {
        const elapsed = (now - marker.completingStartedAt) / 1000;
        const shrink = Math.max(0, 1 - elapsed / FLASH_SECONDS);
        x = marker.end.x;
        y = marker.end.y;
        r = TASK_RADIUS * shrink;
        if (elapsed >= FLASH_SECONDS) {
          tasks.delete(orderId);
          continue;
        }
      }
      ctx.beginPath();
      ctx.arc(x, y, Math.max(0, r), 0, Math.PI * 2);
      ctx.fillStyle = marker.color;
      ctx.fill();
    }

    // broker node
    drawNode(brokerPos.x, brokerPos.y, NODE_RADIUS, BROKER_COLOR, "Broker");

    // worker nodes
    for (const id of workerOrder) {
      const w = workers.get(id);
      if (!w) continue;
      const color = !w.connected ? DISCONNECTED_COLOR : (w.busy ? BUSY_COLOR : IDLE_COLOR);
      drawNode(w.x, w.y, NODE_RADIUS, color, "");
      drawLabel(w.x, w.y + NODE_RADIUS + 14, `${id.slice(0, 6)}\n${w.queues.join(", ")}`);
    }

    requestAnimationFrame(render);
  }

  requestAnimationFrame(render);
})();
