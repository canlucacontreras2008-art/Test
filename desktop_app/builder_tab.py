"""Builder tab: create/edit/delete order form definitions."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .forms import FIELD_TYPES, FieldDef, FormDef, delete_form, list_forms, save_form


class BuilderTab(ttk.Frame):
    def __init__(self, parent: tk.Widget, on_forms_changed=None) -> None:
        super().__init__(parent, padding=10)
        self.on_forms_changed = on_forms_changed
        self._fields: list[FieldDef] = []

        self._build_layout()
        self.refresh_form_list()

    def _build_layout(self) -> None:
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Label(self, text="Saved forms").grid(row=0, column=0, sticky="w")
        self.form_list = tk.Listbox(self, width=22, exportselection=False)
        self.form_list.grid(row=1, column=0, sticky="ns", padx=(0, 10))
        self.form_list.bind("<<ListboxSelect>>", self._on_select_form)

        list_buttons = ttk.Frame(self)
        list_buttons.grid(row=2, column=0, sticky="ew")
        ttk.Button(list_buttons, text="New", command=self._new_form).pack(side="left")
        ttk.Button(list_buttons, text="Delete", command=self._delete_form).pack(side="left")

        editor = ttk.Frame(self)
        editor.grid(row=0, column=1, rowspan=3, sticky="nsew")
        editor.columnconfigure(1, weight=1)

        ttk.Label(editor, text="job_type").grid(row=0, column=0, sticky="w")
        self.job_type_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.job_type_var).grid(row=0, column=1, sticky="ew", pady=4)

        ttk.Label(editor, text="Fields").grid(row=1, column=0, sticky="nw", pady=(10, 0))
        self.fields_frame = ttk.Frame(editor)
        self.fields_frame.grid(row=1, column=1, sticky="ew", pady=(10, 0))
        self.fields_frame.columnconfigure(0, weight=1)

        ttk.Button(editor, text="+ Add field", command=self._add_field_row).grid(
            row=2, column=1, sticky="w", pady=6
        )
        ttk.Button(editor, text="Save form", command=self._save_form).grid(row=3, column=1, sticky="w")

    def _on_select_form(self, _event=None) -> None:
        selection = self.form_list.curselection()
        if not selection:
            return
        job_type = self.form_list.get(selection[0])
        for form in list_forms():
            if form.job_type == job_type:
                self._load_form(form)
                return

    def _load_form(self, form: FormDef) -> None:
        self.job_type_var.set(form.job_type)
        self._fields = list(form.fields)
        self._render_fields()

    def _new_form(self) -> None:
        self.job_type_var.set("")
        self._fields = []
        self._render_fields()
        self.form_list.selection_clear(0, tk.END)

    def _add_field_row(self) -> None:
        self._fields.append(FieldDef(name="", type="str"))
        self._render_fields()

    def _remove_field(self, index: int) -> None:
        del self._fields[index]
        self._render_fields()

    def _render_fields(self) -> None:
        for child in self.fields_frame.winfo_children():
            child.destroy()

        for i, f in enumerate(self._fields):
            name_var = tk.StringVar(value=f.name)
            name_var.trace_add("write", lambda *_, i=i, v=name_var: self._fields.__setitem__(
                i, FieldDef(v.get(), self._fields[i].type)
            ))
            ttk.Entry(self.fields_frame, textvariable=name_var, width=16).grid(row=i, column=0, sticky="ew", pady=2)

            type_var = tk.StringVar(value=f.type)
            type_menu = ttk.OptionMenu(
                self.fields_frame,
                type_var,
                f.type,
                *FIELD_TYPES,
                command=lambda choice, i=i: self._fields.__setitem__(i, FieldDef(self._fields[i].name, choice)),
            )
            type_menu.grid(row=i, column=1, padx=4)

            ttk.Button(self.fields_frame, text="x", width=2, command=lambda i=i: self._remove_field(i)).grid(
                row=i, column=2
            )

    def _save_form(self) -> None:
        job_type = self.job_type_var.get().strip()
        if not job_type:
            messagebox.showerror("Missing job_type", "Give the form a job_type name.")
            return
        field_names = [f.name.strip() for f in self._fields]
        if any(not name for name in field_names):
            messagebox.showerror("Missing field name", "Every field needs a name.")
            return
        save_form(FormDef(job_type=job_type, fields=[FieldDef(n, f.type) for n, f in zip(field_names, self._fields)]))
        self.refresh_form_list()
        if self.on_forms_changed:
            self.on_forms_changed()

    def _delete_form(self) -> None:
        selection = self.form_list.curselection()
        if not selection:
            return
        job_type = self.form_list.get(selection[0])
        if messagebox.askyesno("Delete form", f"Delete form '{job_type}'?"):
            delete_form(job_type)
            self.refresh_form_list()
            self._new_form()
            if self.on_forms_changed:
                self.on_forms_changed()

    def refresh_form_list(self) -> None:
        self.form_list.delete(0, tk.END)
        for form in list_forms():
            self.form_list.insert(tk.END, form.job_type)
