"""Windows URL protocol impulsecalc:// so share links can reopen this app."""

from __future__ import annotations

import sys
from pathlib import Path


PROTOCOL = "impulsecalc"


def launch_command() -> str:
    """Command line written into HKCU for impulsecalc://open."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        return f'"{exe}" --protocol "%1"'
    root = Path(__file__).resolve().parents[1]
    py = Path(sys.executable).resolve()
    server = root / "server.py"
    return f'"{py}" "{server}" --protocol "%1"'


def register_protocol() -> bool:
    """Register HKCU URL protocol. Best-effort; never fatal."""
    if sys.platform != "win32":
        return False
    try:
        import winreg

        cmd = launch_command()
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROTOCOL}")
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, "URL:ImpulseCalc Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")
        icon = winreg.CreateKey(key, "DefaultIcon")
        winreg.SetValueEx(icon, None, 0, winreg.REG_SZ, launch_command().split(" --protocol")[0].strip().strip('"'))
        shell = winreg.CreateKey(key, r"shell\open\command")
        winreg.SetValueEx(shell, None, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(shell)
        winreg.CloseKey(icon)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False
