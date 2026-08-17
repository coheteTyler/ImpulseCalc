# PyInstaller spec — one-file ImpulseCalc.exe
# Build:  pyinstaller --noconfirm ImpulseCalc.spec

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / "static"), "static"),
    (str(ROOT / "configs"), "configs"),
    (str(ROOT / "impulsecalc" / "static"), "impulsecalc/static"),
]

hidden = collect_submodules("impulsecalc")
binaries = []
for pkg in ("numpy", "matplotlib", "flask"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "server.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden + ["impulsecalc.protocol", "impulsecalc.share_catalog"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ImpulseCalc",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
