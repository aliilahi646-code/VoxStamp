#!/usr/bin/env python3
"""
desktop_app.py -- VoxStamp desktop launcher
=============================================
This is the entry point for the Windows .exe build. It starts the same
Flask app used for the web version, but in a background thread, and opens
it in its own native window (via pywebview) instead of a browser tab --
no address bar, no tabs, just the app.

This file is NOT run directly during normal web/local use -- that's still
"python app.py" as always. This one is only for building the .exe (see
BUILD_EXE.md for the exact command).
"""

import socket
import threading
import time

import webview

from app import app

HOST = "127.0.0.1"
PORT = 5000


def _port_is_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, port)) != 0


def start_server():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False, threaded=True)


if __name__ == "__main__":
    # Find a free port in case 5000 is already taken by something else.
    port = PORT
    while not _port_is_free(port):
        port += 1

    server_thread = threading.Thread(
        target=lambda: app.run(host=HOST, port=port, debug=False, use_reloader=False, threaded=True),
        daemon=True,
    )
    server_thread.start()

    # Give the server a moment to start before pointing the window at it.
    time.sleep(1.2)

    webview.create_window(
        "VoxStamp",
        f"http://{HOST}:{port}/",
        width=1180,
        height=820,
        min_size=(760, 560),
    )
    webview.start()
