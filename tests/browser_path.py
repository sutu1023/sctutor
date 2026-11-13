"""Shared browser discovery; no project or macOS-only requirement."""
import os
import shutil
from pathlib import Path


def chrome_path():
    explicit = os.getenv("CHROME_BIN")
    if explicit:
        return Path(explicit)
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
