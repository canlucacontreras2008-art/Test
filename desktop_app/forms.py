"""Storage for user-built order forms.

A form definition maps a job_type to a list of typed fields, so the
Dashboard tab can render a form instead of asking for raw JSON. Forms are
per-user desktop state, kept under ~/.order_broker/forms as one JSON file
per form.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

FIELD_TYPES = ("str", "int", "float", "bool")

FORMS_DIR = Path.home() / ".order_broker" / "forms"


@dataclass
class FieldDef:
    name: str
    type: str = "str"  # one of FIELD_TYPES


@dataclass
class FormDef:
    job_type: str
    fields: list[FieldDef] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "FormDef":
        data = json.loads(raw)
        return cls(
            job_type=data["job_type"],
            fields=[FieldDef(**f) for f in data.get("fields", [])],
        )

    def cast_payload(self, raw_values: dict[str, str]) -> dict:
        """Turn the form's raw string inputs into a typed payload dict."""
        payload: dict = {}
        for f in self.fields:
            raw = raw_values.get(f.name, "")
            if f.type == "int":
                payload[f.name] = int(raw) if raw != "" else 0
            elif f.type == "float":
                payload[f.name] = float(raw) if raw != "" else 0.0
            elif f.type == "bool":
                payload[f.name] = raw.strip().lower() in ("1", "true", "yes", "on")
            else:
                payload[f.name] = raw
        return payload


def _path_for(job_type: str) -> Path:
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in job_type)
    return FORMS_DIR / f"{safe_name}.json"


def save_form(form: FormDef) -> None:
    FORMS_DIR.mkdir(parents=True, exist_ok=True)
    _path_for(form.job_type).write_text(form.to_json())


def delete_form(job_type: str) -> None:
    path = _path_for(job_type)
    if path.exists():
        path.unlink()


def list_forms() -> list[FormDef]:
    if not FORMS_DIR.exists():
        return []
    forms = []
    for path in sorted(FORMS_DIR.glob("*.json")):
        try:
            forms.append(FormDef.from_json(path.read_text()))
        except (json.JSONDecodeError, KeyError):
            continue
    return forms


def seed_default_forms() -> None:
    """Create example forms matching examples/example_worker.py, but only
    the first time (i.e. when no forms exist yet)."""
    if FORMS_DIR.exists() and any(FORMS_DIR.glob("*.json")):
        return
    save_form(FormDef("add", [FieldDef("a", "int"), FieldDef("b", "int")]))
    save_form(FormDef("shout", [FieldDef("text", "str")]))
