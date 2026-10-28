"""BAD AI - start racing with the live race engineer, and turn recorded races into commentated replays.

Run it with:  python launcher.py   (or double-click "BAD AI.pyw" to start it without a console window)
"""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from launcher_app.app import main  # noqa: E402

if __name__ == "__main__":
    main()
