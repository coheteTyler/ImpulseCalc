import subprocess
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

bash = r"""
set +e
. /opt/openfoam12/etc/bashrc >/dev/null 2>&1
echo WM=${WM_PROJECT_DIR:-empty}
echo APP=${FOAM_APPBIN:-empty}
echo ETC=${FOAM_ETC:-empty}
echo which=$(command -v blockMesh)
ls -la ${FOAM_APPBIN}/blockMesh 2>&1 | head -1
cd /mnt/c/Users/tyler/ImpulseCalc/output/openfoam_cases/impulse_r0
blockMesh 2>&1 | tail -30
echo RC=$?
"""
proc = subprocess.run(
    ["wsl", "-d", "Ubuntu", "--", "bash", "-lc", bash],
    capture_output=True,
    text=True,
    timeout=120,
)
print("RC", proc.returncode)
print("OUT:\n", proc.stdout)
print("ERR:\n", proc.stderr[-1000:] if proc.stderr else "")

from impulsecalc import runners

runners._wsl_openfoam_probe.cache_clear()
print("probe", json.dumps(runners.openfoam_available(), indent=2))
r = runners.mesh_pipeline(
    r"C:\Users\tyler\ImpulseCalc\output\openfoam_cases\impulse_r0"
)
print("mesh success", r["success"])
print("detail", r.get("detail"))
print("bm stdout", r["blockMesh"].get("stdout_tail", "")[-800:])
print("bm stderr", r["blockMesh"].get("stderr_tail", "")[-800:])
