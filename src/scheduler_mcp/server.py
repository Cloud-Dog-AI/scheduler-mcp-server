"""Entry point — runs the API server via uvicorn.

The CLOUD_DOG__* env overlay is handled by cloud_dog_config when invoked
with the ``--env`` flag from server_control.sh. ``uvicorn`` itself reads
``server.api.host`` + ``server.api.port`` through cloud_dog_config.
"""

from __future__ import annotations

import sys

import uvicorn

from scheduler_mcp import config
from scheduler_mcp.app import create_app


def main(argv: list[str] | None = None) -> int:
    _argv = argv if argv is not None else sys.argv[1:]
    # `--env <path>` is consumed by cloud_dog_config's loader chain at import time
    # via the CLOUD_DOG_ENV_FILES env var. server_control.sh sets it before exec.
    del _argv
    host: str = str(config.get("server.api.host", "0.0.0.0"))
    port: int = int(config.get("server.api.port", 8080))
    app = create_app()
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
