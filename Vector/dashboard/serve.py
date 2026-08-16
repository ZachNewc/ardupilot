#!/usr/bin/env python3
"""
AP_FLAKE8_CLEAN
Entry point for the Vector dashboard server.

    python3 Vector/dashboard/serve.py [--host 0.0.0.0] [--port 8765]

Serves the built web app and the telemetry WebSocket from one process. Use
``Vector/start.sh`` if you also want the frontend built when it is stale.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Vector dashboard server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true", help="restart on source changes")
    args = parser.parse_args()

    import uvicorn

    print(f"Vector dashboard on http://localhost:{args.port}")
    uvicorn.run(
        "server.app:app",
        host=args.host,
        port=args.port,
        log_level="warning",
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
