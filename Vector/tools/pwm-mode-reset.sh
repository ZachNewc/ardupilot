#!/usr/bin/env bash
# Close Mission Planner first. It holds the Windows COM port.
set -euo pipefail
TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$TOOLS/pwm-mode-reset.py" "$@"
