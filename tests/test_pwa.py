"""PWA / Web Share Target: manifest, service worker, icons, and how the server hands them out."""
import json
import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
client = TestClient(app)


def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">II", data[16:24])


def test_manifest_is_served_as_a_manifest():
    r = client.get("/manifest.json")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/manifest+json")
    assert r.json()["name"]


def test_manifest_is_installable():
    m = client.get("/manifest.json").json()
    assert m["start_url"] == "/" and m["scope"] == "/"
    assert m["display"] in {"standalone", "minimal-ui"}
    assert m["short_name"] and m["theme_color"].startswith("#") and m["background_color"].startswith("#")
    sizes = {i["sizes"] for i in m["icons"]}
    assert {"192x192", "512x512"} <= sizes                      # what Chrome needs to offer "Install"
    assert any(i.get("purpose") == "maskable" for i in m["icons"])


def test_manifest_declares_a_get_share_target():
    t = client.get("/manifest.json").json()["share_target"]
    assert t["action"] == "/" and t["method"] == "GET"
    assert t["enctype"] == "application/x-www-form-urlencoded"   # Chrome warns when it is left out
    assert t["params"] == {"title": "title", "text": "text", "url": "url"}


def test_every_manifest_icon_is_served_and_the_right_size():
    for icon in client.get("/manifest.json").json()["icons"]:
        r = client.get(icon["src"])
        assert r.status_code == 200, icon["src"]
        assert r.headers["content-type"] == "image/png"
        w, h = (int(n) for n in icon["sizes"].split("x"))
        assert png_size(r.content) == (w, h), icon["src"]


def test_service_worker_is_served_from_the_root_and_not_cached():
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    assert r.headers["cache-control"] == "no-cache"             # browsers must re-check it on every visit
    assert "fetch" in r.text


def test_page_links_the_manifest_and_registers_the_worker():
    html = client.get("/").text
    assert '<link rel="manifest" href="/manifest.json">' in html
    assert 'name="theme-color"' in html and 'rel="apple-touch-icon"' in html
    assert 'register("/sw.js")' in html


def test_service_worker_never_stores_api_responses():
    """Static guard on top of sw_check.js: the worker never mentions caching an API path or a request body."""
    src = (FRONTEND / "sw.js").read_text(encoding="utf-8")
    assert 'startsWith("/api/")) return' in src
    assert 'req.method !== "GET") return' in src
    assert not re.search(r"cache\.put\((?!SHELL)", src)         # the only thing ever stored is the shell page


def test_api_is_unchanged_by_the_pwa_routes():
    assert client.get("/api/health").json()["ok"] is True
    r = client.post("/api/check", data={"text": "Your SBI account is blocked, update KYC at http://sbi-kyc.xyz", "ai": "false"})
    assert r.status_code == 200 and r.json()["verdict"] in {"scam", "suspicious"}
    assert client.get("/static/index.html").status_code == 200   # the older static mount still works


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")
def test_service_worker_behaviour():
    script = Path(__file__).resolve().parent / "sw_check.js"
    r = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
