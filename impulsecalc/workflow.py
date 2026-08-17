"""Design save/load + standalone export."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .meanline import MeanlineInputs


@dataclass
class WorkflowState:
    meanline_inputs: dict[str, Any] = field(default_factory=dict)
    case_dir: str | None = None
    output_dir: str = "output"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowState":
        return cls(
            meanline_inputs=dict(d.get("meanline_inputs") or {}),
            case_dir=d.get("case_dir"),
            output_dir=str(d.get("output_dir") or "output"),
            notes=list(d.get("notes") or []),
        )


def save_design(path: str | Path, state: WorkflowState | MeanlineInputs | dict) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(state, MeanlineInputs):
        payload = {"meanline_inputs": state.to_dict(), "format": "impulsecalc_v1"}
    elif isinstance(state, WorkflowState):
        payload = {**state.to_dict(), "format": "impulsecalc_v1"}
    else:
        payload = {**state, "format": "impulsecalc_v1"}
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def load_design(path: str | Path) -> WorkflowState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "meanline_inputs" in data:
        return WorkflowState.from_dict(data)
    return WorkflowState(meanline_inputs=data)


def export_standalone_generator_script(meanline_inputs: MeanlineInputs, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(meanline_inputs.to_dict(), indent=2)
    p.write_text(
        f'''#!/usr/bin/env python3
"""Standalone ImpulseCalc case generator (no web UI)."""
import json
from pathlib import Path
from impulsecalc.meanline import MeanlineInputs
from impulsecalc.openfoam_case import generate_openfoam_case

INPUTS = json.loads(r"""
{payload}
""")

if __name__ == "__main__":
    inp = MeanlineInputs.from_dict(INPUTS)
    r = generate_openfoam_case(inp, Path("output"), case_name=inp.blade_name or "cascade")
    print(r.message)
    print(r.case_dir)
''',
        encoding="utf-8",
    )
    return p
