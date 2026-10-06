#!/usr/bin/env python3
"""Thin entry point — implementation lives in the ``arbor_guardian`` package.

python3 arbor_guardian.py <subcommand> …
python3 -m arbor_guardian <subcommand> …
"""

from arbor_guardian.main import run

if __name__ == "__main__":
    run()
