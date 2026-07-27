"""Private entry point for one process-isolated compilation."""

from __future__ import annotations

import sys

from .isolation import run_worker


def main() -> int:
    if len(sys.argv) != 3:
        return 2
    return run_worker(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    raise SystemExit(main())
