"""Entry point for `python -m fina`."""

from __future__ import annotations

import sys

from fina.cli import main

if __name__ == "__main__":
    sys.exit(main())
