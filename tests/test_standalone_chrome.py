"""Standalone chrome + share API — no Library wrapper."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

STATIC = ROOT / "static"


def test_section_anchors_and_chrome_assets():
    body = (STATIC / "calcbody.html").read_text(encoding="utf-8")
    for n in range(1, 8):
        assert f'id="sec{n}"' in body
    assert "app-chrome.css" in body
    assert "app-chrome.js" in body
    assert "AppChrome.install" in body
    assert "§1 Meanline" in body
    assert "Return to Library" not in body
    assert "Done — send to Library" not in body
    assert (STATIC / "app-chrome.js").is_file()
    assert (STATIC / "app-chrome.css").is_file()
    js = (STATIC / "app-chrome.js").read_text(encoding="utf-8")
    assert "coheteTyler.github.io/app-share" in js
    assert "127.0.0.1" not in js.split("SHARE_BASE")[1].split(";")[0]


def test_share_catalog_https_only():
    from impulsecalc.share_catalog import APPS, SHARE_BASE, share_url

    assert SHARE_BASE.startswith("https://")
    assert "127.0.0.1" not in SHARE_BASE
    url = share_url("impulsecalc")
    assert url.startswith("https://")
    assert "impulsecalc" in url
    for row in APPS:
        assert row["id"]
        assert row["port"]
        assert row["local_url"].startswith("http://127.0.0.1:")


def test_api_share_and_siblings():
    from server import app

    c = app.test_client()
    r = c.get("/api/share")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["url"].startswith("https://")
    assert "127.0.0.1" not in data["url"]
    sib = c.get("/api/siblings")
    assert sib.status_code == 200
    payload = sib.get_json()
    ids = {a["id"] for a in payload["apps"]}
    assert "impulsecalc" in ids
    assert "cycle" in ids
    health = c.get("/api/health")
    assert health.get_json()["standalone"] is True


def test_start_script_exists():
    assert (ROOT / "start.ps1").is_file()
    text = (ROOT / "start.ps1").read_text(encoding="utf-8")
    assert "8765" in text
    assert "venv" in text.lower()
    assert "LPRE" in text  # isolation note
