#!/usr/bin/env python3
"""Selectel floating IP helper — entry point."""

from __future__ import annotations

import sys

from sfip.cli import main


if __name__ == "__main__":
    sys.exit(main())
