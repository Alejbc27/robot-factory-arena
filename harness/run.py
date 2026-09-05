"""``python -m harness.run`` — module-form alias of the tournament entry point."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
