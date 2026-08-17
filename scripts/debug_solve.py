"""Debug ImpulseCalc solve path."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from impulsecalc.runners import (  # noqa: E402
    _wsl_openfoam_probe,
    _wsl_path,
    _wsl_source_prefix,
    run_solver,
)

case = ROOT / "output" / "openfoam_cases" / "impulse_r0"
_wsl_openfoam_probe.cache_clear()

bash = (
    _wsl_source_prefix()
    + "echo APPBIN=$FOAM_APPBIN; "
    + "echo which_rho=$(command -v rhoCentralFoam); "
    + "echo which_foamRun=$(command -v foamRun); "
    + "ls $FOAM_APPBIN 2>/dev/null | grep -iE 'rho|central|shock|sonic|foamRun' | head -30; "
    + f'cd "{_wsl_path(case)}" && rhoCentralFoam 2>&1 | tail -80; '
    + "echo SOLVE_RC=$?"
)
print("=== direct WSL rhoCentralFoam ===")
proc = subprocess.run(
    ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", bash],
    capture_output=True,
    text=True,
    timeout=120,
)
print(proc.stdout)
print(proc.stderr[-1500:] if proc.stderr else "")

print("\n=== run_solver() ===")
r = run_solver(case, timeout_s=120)
print(json.dumps(r.to_dict(), indent=2)[:4000])
