"""Allow `python3 -m vapi_caller`."""

from __future__ import annotations

import sys

from vapi_caller.cli import main

if __name__ == "__main__":
    sys.exit(main())
