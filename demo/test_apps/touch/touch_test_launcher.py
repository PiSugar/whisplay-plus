#!/usr/bin/env python3
from __future__ import annotations

import http.server
import os
import signal
import shutil
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
INDEX = APP_DIR / "index.html"


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


class TouchHandler(http.server.SimpleHTTPRequestHandler):
    server: "TouchServer"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(APP_DIR), **kwargs)

    def log_message(self, format: str, *args) -> None:
        return

    def do_POST(self) -> None:
        if self.path != "/quit":
            self.send_error(404)
            return
        self.send_response(204)
        self.send_header("Connection", "close")
        self.end_headers()
        threading.Thread(target=self.server.quit_app, daemon=True).start()


class TouchServer(ReusableTCPServer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.browser: subprocess.Popen[bytes] | None = None

    def quit_app(self) -> None:
        time.sleep(0.15)
        if self.browser and self.browser.poll() is None:
            self.browser.terminate()
            try:
                self.browser.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                self.browser.kill()
        self.shutdown()


def find_browser() -> str:
    for candidate in ("chromium-browser", "chromium", "google-chrome-stable", "google-chrome", "firefox-esr", "firefox", "angelfish"):
        if shutil.which(candidate):
            return candidate
    raise RuntimeError("No supported browser found")


def browser_cmd(browser: str, url: str, mode: str) -> list[str]:
    if browser.startswith("chromium") or browser.startswith("google-chrome"):
        flags = [
            browser,
            "--disable-pinch",
            "--overscroll-history-navigation=0",
            "--disable-features=TouchpadOverscrollHistoryNavigation",
        ]
        if mode in ("", "--fullscreen", "--kiosk"):
            return flags + ["--kiosk", url]
        return flags + ["--new-window", f"--app={url}"]
    if browser.startswith("firefox"):
        return [browser, "--new-window" if mode == "--windowed" else "--kiosk", url]
    return [browser, url]


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode not in ("", "--fullscreen", "--windowed", "--kiosk"):
        raise SystemExit(f"Unknown option: {mode}")
    if not INDEX.exists():
        raise SystemExit(f"Missing app file: {INDEX}")

    with TouchServer(("127.0.0.1", 0), TouchHandler) as server:
        port = server.server_address[1]
        url = f"http://127.0.0.1:{port}/index.html"
        browser = find_browser()
        server.browser = subprocess.Popen(browser_cmd(browser, url, mode), cwd=str(APP_DIR))
        try:
            server.serve_forever()
        finally:
            if server.browser and server.browser.poll() is None:
                server.browser.terminate()
                try:
                    server.browser.wait(timeout=1.5)
                except subprocess.TimeoutExpired:
                    server.browser.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
