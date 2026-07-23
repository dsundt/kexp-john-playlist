#!/usr/bin/env python3
"""Thin entrypoint — all orchestration lives in kexp.pipeline.main().

Runs as `python scripts/run.py` from the repo root, which puts scripts/ (not the
repo root) on sys.path — so add the repo root before importing the kexp package.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kexp.pipeline import main  # noqa: E402  (import after sys.path fix)

if __name__ == "__main__":
    main()
