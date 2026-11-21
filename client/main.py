# client/main.py
"""Horizon Relay desktop client entry point.

    python -m client.main [--host 127.0.0.1] [--port 5000] [--connect]
"""
from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Horizon Relay desktop client")
    parser.add_argument("--host", help="Server address to prefill")
    parser.add_argument("--port", type=int, help="Server port to prefill (default 5000)")
    parser.add_argument("--connect", action="store_true", help="Connect immediately on launch")
    args, qt_args = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if sys.platform == "win32":
        try:  # own taskbar group + icon instead of python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HorizonRelay.Client")
        except Exception:  # noqa: BLE001
            pass

    from client.ui.main_window import MainWindow, build_app

    app = build_app([sys.argv[0], *qt_args])
    win = MainWindow()
    if args.host:
        win.connection.host.setText(args.host)
    if args.port:
        win.connection.port.setText(str(args.port))
    win.show()
    if args.connect:
        win.connection._submit()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
