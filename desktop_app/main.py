"""Desktop app: build order forms and use them to drive the broker.

Usage: python -m desktop_app
Requires a Tk-enabled Python (the python3-tk / python3.x-tk OS package).
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .builder_tab import BuilderTab
from .dashboard_tab import DashboardTab
from .forms import seed_default_forms


def main() -> None:
    seed_default_forms()

    root = tk.Tk()
    root.title("Order Broker Control Panel")
    root.geometry("760x620")

    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    dashboard = DashboardTab(notebook)
    builder = BuilderTab(notebook, on_forms_changed=dashboard.refresh_forms)

    notebook.add(dashboard, text="Dashboard")
    notebook.add(builder, text="Builder")

    def on_close() -> None:
        dashboard.shutdown()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
