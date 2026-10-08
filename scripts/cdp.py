"""A very small Chrome DevTools Protocol driver for headless Edge/Chrome, used by capture_screenshots.py.

Uses only `websockets` (already installed with uvicorn[standard]) and the standard library. It starts its own
browser with a throwaway profile and, on close, kills only that process tree, never other browser windows.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> str:
    for p in BROWSERS:
        if Path(p).exists():
            return p
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        if shutil.which(name):
            return shutil.which(name)
    raise RuntimeError("No Edge/Chrome found. Install one or add its path to BROWSERS in scripts/cdp.py.")


class Browser:
    def __init__(self, port: int = 9333, extra_args: list[str] | None = None):
        self.profile = tempfile.mkdtemp(prefix="satark-cdp-")
        args = [find_browser(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={self.profile}",
                "--no-first-run", "--no-default-browser-check", "--disable-extensions", "--hide-scrollbars",
                "--force-color-profile=srgb", *(extra_args or []), "about:blank"]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.port, self._id, self.events = port, 0, []
        self.ws = None
        for _ in range(100):
            try:
                pages = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2))
                page = next(p for p in pages if p["type"] == "page")
                self.ws = connect(page["webSocketDebuggerUrl"], max_size=64 * 1024 * 1024)
                break
            except Exception:
                time.sleep(0.2)
        if self.ws is None:
            self.close()
            raise RuntimeError("Could not connect to the browser's debugging port.")
        for domain in ("Page", "Runtime", "DOM", "Network"):
            self.send(f"{domain}.enable")

    def send(self, method: str, **params):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv(timeout=120))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait_event(self, name: str, timeout: float = 30):
        end = time.time() + timeout
        while time.time() < end:
            for i, e in enumerate(self.events):
                if e["method"] == name:
                    return self.events.pop(i)
            try:
                msg = json.loads(self.ws.recv(timeout=0.5))
            except TimeoutError:
                continue
            if "method" in msg:
                self.events.append(msg)
        raise TimeoutError(name)

    def viewport(self, width: int, height: int, scale: float = 1, mobile: bool = False):
        self.send("Emulation.setDeviceMetricsOverride", width=width, height=height, deviceScaleFactor=scale, mobile=mobile)

    def color_scheme(self, scheme: str):
        """Force prefers-color-scheme to 'light' or 'dark' regardless of the machine's setting."""
        self.send("Emulation.setEmulatedMedia", features=[{"name": "prefers-color-scheme", "value": scheme}])

    def goto(self, url: str):
        self.events.clear()
        self.send("Page.navigate", url=url)
        self.wait_event("Page.loadEventFired", 90)

    def js(self, expression: str, await_promise: bool = False):
        r = self.send("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in r:
            raise RuntimeError(r["exceptionDetails"].get("exception", {}).get("description", "script error"))
        return r["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 30, interval: float = 0.25):
        end = time.time() + timeout
        while time.time() < end:
            if self.js(expression):
                return True
            time.sleep(interval)
        raise TimeoutError(expression)

    def set_files(self, selector: str, paths: list[str]):
        root = self.send("DOM.getDocument")["root"]["nodeId"]
        node = self.send("DOM.querySelector", nodeId=root, selector=selector)["nodeId"]
        self.send("DOM.setFileInputFiles", files=paths, nodeId=node)

    def screenshot(self, path: str | Path, full_page: bool = True):
        """PNG of the page. full_page grows the capture to the document height (at the current width)."""
        params = {"format": "png", "captureBeyondViewport": True}
        if full_page:
            m = self.send("Page.getLayoutMetrics")["cssContentSize"]
            params["clip"] = {"x": 0, "y": 0, "width": m["width"], "height": m["height"], "scale": 1}
        data = self.send("Page.captureScreenshot", **params)["data"]
        Path(path).write_bytes(base64.b64decode(data))

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        finally:
            if self.proc.poll() is None:   # taskkill /T takes down this browser's own helper processes only
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)], capture_output=True)
            time.sleep(0.5)
            shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
