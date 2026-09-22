"""Dashboard tab: connect to a broker, submit orders (via a saved form or
raw JSON), and watch live status updates in a table."""

from __future__ import annotations

import json
import queue
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk

from .broker_thread import BrokerThread
from .forms import FormDef, list_forms

POLL_MS = 100


class DashboardTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, broker: BrokerThread) -> None:
        super().__init__(parent, padding=10)
        self.broker = broker
        self.events = broker.add_listener()
        self._forms: dict[str, FormDef] = {}
        self._field_vars: dict[str, tk.StringVar] = {}
        self._pending_rows: deque[tuple[str, tuple, str]] = deque()  # (job_type, payload, tree_item_id)
        self._order_rows: dict[str, str] = {}  # order_id -> tree_item_id

        self._build_layout()
        self.refresh_forms()
        self.after(POLL_MS, self._poll_events)

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)

        conn = ttk.LabelFrame(self, text="Connection", padding=8)
        conn.grid(row=0, column=0, sticky="ew")
        conn.columnconfigure(1, weight=1)
        ttk.Label(conn, text="Broker URI").grid(row=0, column=0, sticky="w")
        self.uri_var = tk.StringVar(value="ws://localhost:8765")
        ttk.Entry(conn, textvariable=self.uri_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(conn, text="Connect", command=self._connect).grid(row=0, column=2)
        self.status_var = tk.StringVar(value="not connected")
        ttk.Label(conn, textvariable=self.status_var).grid(row=0, column=3, padx=6)

        submit_frame = ttk.LabelFrame(self, text="Submit order", padding=8)
        submit_frame.grid(row=1, column=0, sticky="ew", pady=8)
        submit_frame.columnconfigure(1, weight=1)

        ttk.Label(submit_frame, text="Form").grid(row=0, column=0, sticky="w")
        self.form_var = tk.StringVar()
        self.form_menu = ttk.OptionMenu(submit_frame, self.form_var, "")
        self.form_menu.grid(row=0, column=1, sticky="w")
        ttk.Button(submit_frame, text="Refresh forms", command=self.refresh_forms).grid(row=0, column=2)

        self.fields_frame = ttk.Frame(submit_frame)
        self.fields_frame.grid(row=1, column=0, columnspan=3, sticky="ew", pady=6)
        ttk.Button(submit_frame, text="Submit via form", command=self._submit_via_form).grid(
            row=2, column=0, sticky="w"
        )

        ttk.Separator(submit_frame).grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)

        ttk.Label(submit_frame, text="Raw job_type").grid(row=4, column=0, sticky="w")
        self.raw_job_type_var = tk.StringVar()
        ttk.Entry(submit_frame, textvariable=self.raw_job_type_var).grid(row=4, column=1, sticky="ew")
        ttk.Label(submit_frame, text="Raw JSON payload").grid(row=5, column=0, sticky="nw")
        self.raw_payload_text = tk.Text(submit_frame, height=3)
        self.raw_payload_text.grid(row=5, column=1, columnspan=2, sticky="ew")
        ttk.Button(submit_frame, text="Submit raw", command=self._submit_raw).grid(row=6, column=0, sticky="w", pady=4)

        table_frame = ttk.LabelFrame(self, text="Orders", padding=8)
        table_frame.grid(row=2, column=0, sticky="nsew")
        self.rowconfigure(2, weight=1)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)

        columns = ("order_id", "job_type", "status", "result", "error")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        for col, width in zip(columns, (200, 100, 100, 200, 200)):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")

    # -- connection / forms -------------------------------------------------

    def _connect(self) -> None:
        self.status_var.set("connecting...")
        self.broker.connect(self.uri_var.get().strip())

    def refresh_forms(self) -> None:
        self._forms = {f.job_type: f for f in list_forms()}
        names = list(self._forms.keys())
        menu = self.form_menu["menu"]
        menu.delete(0, "end")
        for name in names:
            menu.add_command(label=name, command=lambda n=name: self._select_form(n))
        if names and self.form_var.get() not in names:
            self._select_form(names[0])
        elif not names:
            self.form_var.set("")
            self._render_form_fields(None)

    def _select_form(self, job_type: str) -> None:
        self.form_var.set(job_type)
        self._render_form_fields(self._forms.get(job_type))

    def _render_form_fields(self, form: FormDef | None) -> None:
        for child in self.fields_frame.winfo_children():
            child.destroy()
        self._field_vars = {}
        if form is None:
            return
        for i, f in enumerate(form.fields):
            ttk.Label(self.fields_frame, text=f"{f.name} ({f.type})").grid(row=i, column=0, sticky="w")
            var = tk.StringVar()
            ttk.Entry(self.fields_frame, textvariable=var).grid(row=i, column=1, sticky="ew", padx=4)
            self._field_vars[f.name] = var

    # -- submitting -----------------------------------------------------------

    def _submit_via_form(self) -> None:
        job_type = self.form_var.get()
        form = self._forms.get(job_type)
        if form is None:
            messagebox.showerror("No form selected", "Pick a form first, or build one in the Builder tab.")
            return
        raw_values = {name: var.get() for name, var in self._field_vars.items()}
        try:
            payload = form.cast_payload(raw_values)
        except ValueError as exc:
            messagebox.showerror("Invalid field value", str(exc))
            return
        self._submit(job_type, payload)

    def _submit_raw(self) -> None:
        job_type = self.raw_job_type_var.get().strip()
        if not job_type:
            messagebox.showerror("Missing job_type", "Enter a job_type for the raw order.")
            return
        raw_text = self.raw_payload_text.get("1.0", "end").strip()
        try:
            payload = json.loads(raw_text) if raw_text else None
        except json.JSONDecodeError as exc:
            messagebox.showerror("Invalid JSON", str(exc))
            return
        self._submit(job_type, payload)

    def _submit(self, job_type: str, payload) -> None:
        item_id = self.tree.insert("", "end", values=("(pending)", job_type, "submitting", "", ""))
        self._pending_rows.append((job_type, payload, item_id))
        self.broker.submit(job_type, payload)

    # -- event polling --------------------------------------------------------

    def _poll_events(self) -> None:
        try:
            while True:
                kind, data = self.events.get_nowait()
                self._handle_event(kind, data)
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll_events)

    def _handle_event(self, kind: str, data) -> None:
        if kind == "connected":
            self.status_var.set(f"connected to {data}")
        elif kind == "connect_error":
            self.status_var.set("connection failed")
            messagebox.showerror("Connection failed", data)
        elif kind == "submit_error":
            messagebox.showerror("Submit failed", data)
        elif kind == "order_event":
            self._handle_order_event(data)

    def _handle_order_event(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "order_accepted":
            if not self._pending_rows:
                return
            _job_type, _payload, item_id = self._pending_rows.popleft()
            order_id = msg["order_id"]
            self._order_rows[order_id] = item_id
            self.tree.set(item_id, "order_id", order_id)
            self.tree.set(item_id, "status", msg.get("status", ""))
        elif msg_type == "order_update":
            order_id = msg.get("order_id")
            item_id = self._order_rows.get(order_id)
            if item_id is None:
                return
            self.tree.set(item_id, "status", msg.get("status", ""))
            self.tree.set(item_id, "result", "" if msg.get("result") is None else str(msg.get("result")))
            self.tree.set(item_id, "error", msg.get("error") or "")
