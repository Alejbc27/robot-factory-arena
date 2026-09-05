"""``python -m harness`` — the tournament entry point (see :mod:`harness.cli`)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
