"""Locate the installed scrcpy bundle without importing desktop capture code."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_scrcpy(configured=""):
    if configured:
        path = Path(os.path.expandvars(configured)).expanduser()
        if path.is_file():
            return path.resolve()
        raise FileNotFoundError(f"Configured scrcpy.exe does not exist: {path}")
    candidates = []
    found = shutil.which("scrcpy")
    if found:
        candidates.append(Path(found))
    home = Path.home()
    candidates += sorted(home.glob("Downloads/scrcpy-v*-extracted/scrcpy-win64-*/scrcpy.exe"), reverse=True)
    candidates += sorted(home.glob("scrcpy-win64-*/scrcpy.exe"), reverse=True)
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError("scrcpy.exe not found; install it or set scrcpy_path in Settings")
