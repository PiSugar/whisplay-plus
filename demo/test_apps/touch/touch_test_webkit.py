#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("GDK_BACKEND", "wayland")

try:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import GLib, Gtk, WebKit2
except Exception as exc:
    print(
        "WebKitGTK is not available. Install it with:\n"
        "  sudo apt-get install -y gir1.2-webkit2-4.1\n"
        f"Import error: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(70)


APP_DIR = Path(__file__).resolve().parent
INDEX = APP_DIR / "index.html"


class TouchTestWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Whisplay Touch Test")
        self.set_default_size(384, 512)
        self.fullscreen()
        self.connect("destroy", Gtk.main_quit)

        self.webview = WebKit2.WebView()
        settings = self.webview.get_settings()
        settings.set_enable_write_console_messages_to_stdout(False)
        self.webview.connect("decide-policy", self.on_decide_policy)
        self.add(self.webview)
        self.webview.load_uri(INDEX.as_uri())

    def on_decide_policy(self, webview, decision, decision_type) -> bool:
        if decision_type != WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            return False
        request = decision.get_navigation_action().get_request()
        uri = request.get_uri()
        if uri in ("about:blank", "whisplay-touch://quit"):
            decision.ignore()
            GLib.idle_add(Gtk.main_quit)
            return True
        return False


def main() -> int:
    if not INDEX.exists():
        print(f"Missing app file: {INDEX}", file=sys.stderr)
        return 1
    win = TouchTestWindow()
    win.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
