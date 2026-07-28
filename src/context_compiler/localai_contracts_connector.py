"""Optional canonical ``localai-contracts`` NDJSON subprocess connector."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .localai_contracts_adapter import (
    LocalAIContractsAdapter,
    LocalAIContractsUnavailableError,
)


def main(argv: Sequence[str] | None = None) -> int:
    """Serve canonical NDJSON on stdin/stdout until EOF."""

    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        sys.stderr.write(
            "ctxc localai-contracts connector accepts no command arguments\n"
        )
        return 2
    try:
        adapter = LocalAIContractsAdapter()
    except LocalAIContractsUnavailableError:
        sys.stderr.write(
            "localai-contracts 0.2.0a1 is required for this optional connector\n"
        )
        return 2
    adapter.serve_ndjson(sys.stdin.buffer, sys.stdout.buffer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
