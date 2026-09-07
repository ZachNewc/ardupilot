#!/usr/bin/env bash
# Start the Vector dashboard.
#
#   Vector/start.sh              # attach USB (recover Shared-not-Attached), build if stale, serve
#   Vector/start.sh --no-usb     # skip the WSL USB step
#   Vector/start.sh --dev        # Vite hot-reload alongside the Python server
#   PORT=9000 Vector/start.sh
#
# Then open http://localhost:8765 (or $PORT).
set -euo pipefail

VECTOR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$VECTOR/tools/dashboard.sh" "$@"
