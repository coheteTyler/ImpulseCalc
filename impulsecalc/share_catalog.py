"""Canonical share + sibling catalog (HTTPS links, never localhost)."""

from __future__ import annotations

SHARE_BASE = "https://coheteTyler.github.io/app-share"

APPS: list[dict] = [
    {
        "id": "impulsecalc",
        "name": "ImpulseCalc",
        "short": "IC",
        "port": 8765,
        "local_url": "http://127.0.0.1:8765/calc.html",
        "health": "http://127.0.0.1:8765/api/health",
        "protocol": "impulsecalc",
        "share": "impulsecalc",
        "portal_id": "impulsecalc",
        "download": "https://github.com/coheteTyler/ImpulseCalc/releases/latest/download/ImpulseCalc.exe",
        "github": "https://github.com/coheteTyler/ImpulseCalc",
    },
    {
        "id": "cycle",
        "name": "Cycle",
        "short": "Cy",
        "port": 8766,
        "local_url": "http://127.0.0.1:8766/",
        "health": "http://127.0.0.1:8766/api/health",
        "protocol": "lpre-cycle",
        "share": "cycle",
        "portal_id": "lpre-cycle",
        "download": "",
        "github": "https://github.com/coheteTyler/LPRE-Library",
    },
    {
        "id": "pump",
        "name": "Pump",
        "short": "Pu",
        "port": 8767,
        "local_url": "http://127.0.0.1:8767/",
        "health": "http://127.0.0.1:8767/api/health",
        "protocol": "lpre-pump",
        "share": "pump",
        "portal_id": "lpre-pump",
        "download": "",
        "github": "https://github.com/coheteTyler/LPRE-Library",
    },
    {
        "id": "powerhead",
        "name": "Powerhead",
        "short": "PH",
        "port": 8768,
        "local_url": "http://127.0.0.1:8768/",
        "health": "http://127.0.0.1:8768/api/health",
        "protocol": "lpre-powerhead",
        "share": "powerhead",
        "portal_id": "lpre-powerhead",
        "download": "",
        "github": "https://github.com/coheteTyler/LPRE-Library",
    },
    {
        "id": "flight",
        "name": "Flight",
        "short": "Fl",
        "port": 8769,
        "local_url": "http://127.0.0.1:8769/",
        "health": "http://127.0.0.1:8769/api/health",
        "protocol": "lpre-flight",
        "share": "flight",
        "portal_id": "lpre-flight",
        "download": "",
        "github": "https://github.com/coheteTyler/LPRE-Library",
    },
    {
        "id": "library",
        "name": "Library",
        "short": "LP",
        "port": 8770,
        "local_url": "http://127.0.0.1:8770/",
        "health": "http://127.0.0.1:8770/api/health",
        "protocol": "lpre-library",
        "share": "library",
        "portal_id": "lpre-library",
        "download": "",
        "github": "https://github.com/coheteTyler/LPRE-Library",
    },
    {
        "id": "structures",
        "name": "Structures",
        "short": "St",
        "port": 8771,
        "local_url": "http://127.0.0.1:8771/",
        "health": "http://127.0.0.1:8771/api/health",
        "protocol": "lpre-structures",
        "share": "structures",
        "portal_id": "lpre-structures",
        "download": "",
        "github": "https://github.com/coheteTyler/LPRE-Library",
    },
    {
        "id": "tapin",
        "name": "Tap-In",
        "short": "TI",
        "port": 8501,
        "local_url": "http://127.0.0.1:8501/",
        "health": "http://127.0.0.1:8501/",
        "protocol": "tapin",
        "share": "tapin-workbench",
        "portal_id": "tapin-workbench",
        "download": "",
        "github": "https://github.com/coheteTyler/tapin-turbine-workbench",
    },
]

# App Portal / seed ids → catalog id
PORTAL_ALIASES = {
    "impulsecalc": "impulsecalc",
    "lpre-library": "library",
    "lpre-cycle": "cycle",
    "lpre-pump": "pump",
    "lpre-powerhead": "powerhead",
    "lpre-flight": "flight",
    "lpre-structures": "structures",
    "tapin-workbench": "tapin",
    "cycle_app": "cycle",
    "pump_app": "pump",
    "powerhead_app": "powerhead",
    "flight_app": "flight",
    "structures_app": "structures",
    "library": "library",
}


def by_id(app_id: str) -> dict | None:
    key = PORTAL_ALIASES.get(app_id, app_id)
    for row in APPS:
        if row["id"] == key or row.get("portal_id") == app_id:
            return row
    return None


def share_url(app_id: str) -> str | None:
    row = by_id(app_id)
    if not row:
        return None
    return f"{SHARE_BASE}/{row['share']}/"
