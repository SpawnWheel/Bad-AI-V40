"""Double-click to start BAD AI without a console window (same as running launcher.py)."""
import os
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from launcher_app.app import main  # noqa: E402

main()
