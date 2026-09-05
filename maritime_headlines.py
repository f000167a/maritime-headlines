#!/usr/bin/env python3
"""Backward-compatible launcher: python maritime_headlines.py."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from maritime_headlines.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
