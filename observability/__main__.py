"""``python -m observability`` entry point (one-shot CLI; no background process)."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
