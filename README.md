# ImpulseCalc

Standalone **impulse turbine calculator** — mean-line triangles, blade metal, 2D cascade CFD, loss board.

This app runs **by itself**. Starting it does **not** start LPRE-Library, Tap-In, or any other tool.

---

## Run (Windows)

You need Python **3.11+** once (only for this folder). Then:

```powershell
cd C:\Users\tyler\ImpulseCalc
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

The browser opens **http://127.0.0.1:8765/calc.html**

Keep that PowerShell window open. Close it to stop the calculator.

Already set up? Same command works every time.

### Friend got a Discord / WhatsApp link?

Click the link. First time: run the downloaded `ImpulseCalc.exe` (no Python, no Git). After that, the same link opens the calculator immediately.

Share page: https://coheteTyler.github.io/app-share/impulsecalc/

---

## What you see

| Piece | What it is |
|-------|------------|
| **Header** | Jump to §1 Meanline · §2 Blade · §3 Case · §4 CFD · §5 Pressure · §6 Losses · §7 Export |
| **Left icons** | Other tools (Cycle, Pump, …). Click to open if they are running. Right-click → **Share**. |
| **No Library bar** | This is not the LPRE shelf. There is no “return to library” chrome. |

---

## Isolation

| App | Port | How to start |
|-----|------|----------------|
| **ImpulseCalc** | **8765** | `ImpulseCalc\start.ps1` |
| Cycle | 8766 | `LPRE-Library\start-app.ps1 cycle` |
| Pump | 8767 | `LPRE-Library\start-app.ps1 pump` |
| Powerhead | 8768 | `LPRE-Library\start-app.ps1 powerhead` |
| Flight | 8769 | `LPRE-Library\start-app.ps1 flight` |
| LPRE Library | 8770 | `LPRE-Library\start-app.ps1 library` |
| Structures | 8771 | `LPRE-Library\start-app.ps1 structures` |
| Tap-In Workbench | 8501 | `tapin-turbine-workbench\start.ps1` |

---

## Optional: OpenFOAM (WSL)

Mean-line, blade, and the design board work **without** OpenFOAM. Cascade mesh/solve needs OpenFOAM-12 in WSL. See `scripts/install_openfoam_wsl.ps1`.

## Headless job

```powershell
python -c "from impulsecalc.cascade_job import run_cascade_job_file; print(run_cascade_job_file('configs/example_cascade_job.json').message)"
```

## Tests

```powershell
.\.venv\Scripts\Activate.ps1
python -m pytest tests -q
```

## Portable exe (share payload)

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_portable.ps1
```

Writes `dist/ImpulseCalc.exe`. First run registers `impulsecalc://` so share links reopen the app.

## Layout

```
ImpulseCalc/
  start.ps1              # one-shot: venv + deps + server + browser
  server.py              # Flask
  static/                # Devenport-style HTML + chrome
  impulsecalc/           # physics + protocol + share catalog
  configs/  tests/
```

Engineering aid only — not flight certification.
