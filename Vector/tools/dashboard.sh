#!/usr/bin/env bash
# Launch the Vector dashboard.
#
# Builds the web app when it is stale, then serves it and the telemetry WebSocket
# from one Python process.
#
# Prefer Vector/start.sh; this file is what it execs.
#
#   Vector/start.sh              # attach USB if needed, build, serve
#   Vector/start.sh --no-usb     # skip the WSL USB step
#   Vector/start.sh --dev        # Vite dev server alongside, hot reload
#   PORT=9000 Vector/start.sh
set -euo pipefail

TOOLS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VECTOR="$(cd "$TOOLS/.." && pwd)"
WEB="$VECTOR/dashboard/web"
PORT="${PORT:-8765}"

want_usb=1
dev_mode=0
for arg in "$@"; do
  case "$arg" in
    --no-usb) want_usb=0 ;;
    --dev) dev_mode=1 ;;
    -h | --help)
      # Print the leading comment block, stopping at the first line of code.
      awk 'NR > 1 { if (!/^#/) exit; sub(/^# ?/, ""); print }' "$0"
      exit 0
      ;;
    *)
      echo "unknown option: $arg" >&2
      exit 2
      ;;
  esac
done

have_serial() {
  local path
  for path in /dev/ttyACM* /dev/ttyUSB*; do
    [[ -e "$path" ]] && return 0
  done
  return 1
}

# ---------------------------------------------------------------- dependencies
missing=()
for module in serial pymavlink starlette uvicorn; do
  python3 -c "import $module" 2>/dev/null || missing+=("$module")
done
if ((${#missing[@]})); then
  echo "Missing Python modules: ${missing[*]}"
  echo "Install them with:"
  echo "  python3 -m pip install --user pyserial pymavlink starlette uvicorn"
  exit 1
fi

# ------------------------------------------------------------------------- USB
if ((want_usb)) && ! have_serial; then
  if [[ -x "$TOOLS/wsl-usb.sh" ]]; then
    "$TOOLS/wsl-usb.sh" || echo "USB attach did not succeed; starting anyway."
  else
    echo "No serial device found. Attach the flight controller, then reconnect from the UI."
  fi
fi

# --------------------------------------------------------------------- web app
cd "$WEB"
if [[ ! -d node_modules ]]; then
  echo "Installing frontend dependencies..."
  npm install
fi

if ((dev_mode)); then
  echo "Starting Vite dev server on http://localhost:5173 (proxies to :$PORT)"
  npm run dev &
  vite_pid=$!
  trap 'kill "$vite_pid" 2>/dev/null || true' EXIT
else
  # Rebuild when any source is newer than the last build.
  if [[ ! -f dist/index.html ]] ||
    [[ -n "$(find src index.html package.json vite.config.ts tsconfig.json -newer dist/index.html 2>/dev/null)" ]]; then
    echo "Building frontend..."
    npm run build
  fi
fi

# ---------------------------------------------------------------------- server
cd "$VECTOR/dashboard"
echo "Vector dashboard on http://localhost:${PORT}"
exec python3 serve.py --port "$PORT"
