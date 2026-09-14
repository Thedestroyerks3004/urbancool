#!/usr/bin/env bash
# Single-command end-to-end launcher for Urban Cool.
# Starts the FastAPI backend and Next.js frontend, waits for BOTH to actually
# respond (not just "process started"), then tails both logs until Ctrl+C,
# at which point it kills both cleanly.

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
LOG_DIR="$ROOT_DIR/.run-logs"

# Auto-detect Python from PATH instead of a hardcoded machine-specific install path --
# a hardcoded path here means the script silently fails to even launch the backend on
# any machine other than the one it was written on, and the failure just LOOKS like a
# slow startup (wait_for_http below spins for the full timeout before reporting it).
PYTHON_BIN="$(command -v python || command -v python3 || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "ERROR: No 'python' (or 'python3') found on PATH. Install Python 3.11+ and make sure it's on PATH, then re-run this script."
  exit 1
fi
echo "Using Python: $PYTHON_BIN"

BACKEND_HOST="127.0.0.1"
BACKEND_PORT="8000"
FRONTEND_PORT="3000"
BACKEND_URL="http://${BACKEND_HOST}:${BACKEND_PORT}"
FRONTEND_URL="http://localhost:${FRONTEND_PORT}"

mkdir -p "$LOG_DIR"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

BACKEND_PID=""
FRONTEND_PID=""

kill_port() {
  local port="$1"
  local pids
  pids=$(netstat -ano 2>/dev/null | grep "LISTENING" | grep ":${port} " | awk '{print $NF}' | sort -u)
  if [ -n "$pids" ]; then
    echo "Freeing port ${port} (killing PID(s): $(echo "$pids" | tr '\n' ' '))"
    for pid in $pids; do
      taskkill //F //PID "$pid" >/dev/null 2>&1 || true
    done
  fi
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local timeout_seconds="$3"
  local waited=0
  echo "Waiting for ${label} at ${url} ..."
  until curl -s -o /dev/null -w "" "$url" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
    if [ "$waited" -ge "$timeout_seconds" ]; then
      echo "TIMEOUT: ${label} did not respond within ${timeout_seconds}s. Check ${LOG_DIR}."
      return 1
    fi
  done
  echo "${label} is up (took ${waited}s)."
  return 0
}

cleanup() {
  echo ""
  echo "Shutting down..."
  [ -n "$FRONTEND_PID" ] && kill "$FRONTEND_PID" 2>/dev/null
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
  kill_port "$FRONTEND_PORT"
  kill_port "$BACKEND_PORT"
  echo "Stopped."
  exit 0
}
trap cleanup INT TERM

echo "====================================================="
echo "Urban Cool -- end-to-end launch"
echo "====================================================="

echo ""
echo "Step 1/5: checking dependencies are actually installed ..."
if ! "$PYTHON_BIN" -c "import fastapi, uvicorn, catboost, rasterio, geopandas, shap" 2>/dev/null; then
  echo "ERROR: backend Python packages are missing. Run this first:"
  echo "  cd backend && \"$PYTHON_BIN\" -m pip install -r requirements.txt"
  exit 1
fi
if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "ERROR: frontend dependencies are missing. Run this first:"
  echo "  cd frontend && npm install"
  exit 1
fi
if [ ! -s "$BACKEND_DIR/cache/feature_stack_10m.npz" ]; then
  echo "ERROR: backend/cache/feature_stack_10m.npz is missing or empty."
  echo "This is a Git LFS file -- if it's a few hundred bytes of text instead of"
  echo "~170MB, Git LFS wasn't installed before cloning. Run:"
  echo "  git lfs install && git lfs pull"
  exit 1
fi
echo "Dependencies OK."

echo ""
echo "Step 2/5: freeing ports ${BACKEND_PORT} and ${FRONTEND_PORT} if already in use ..."
kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"

echo ""
echo "Step 3/5: starting backend (this loads the cached feature stack + trained model into memory) ..."
(
  cd "$BACKEND_DIR" || exit 1
  "$PYTHON_BIN" -m uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!
echo "Backend PID: $BACKEND_PID (log: $BACKEND_LOG)"

if ! wait_for_http "${BACKEND_URL}/api/v1/health" "Backend" 60; then
  echo "--- last 30 lines of backend log ---"
  tail -n 30 "$BACKEND_LOG"
  cleanup
fi

echo ""
echo "Step 4/5: starting frontend ..."
(
  cd "$FRONTEND_DIR" || exit 1
  npm run dev
) > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!
echo "Frontend PID: $FRONTEND_PID (log: $FRONTEND_LOG)"

if ! wait_for_http "$FRONTEND_URL" "Frontend" 60; then
  echo "--- last 30 lines of frontend log ---"
  tail -n 30 "$FRONTEND_LOG"
  cleanup
fi

echo ""
echo "Step 5/5: ready."
echo "====================================================="
echo "Backend:  $BACKEND_URL  (docs at $BACKEND_URL/docs)"
echo "Frontend: $FRONTEND_URL"
echo "Logs:     $LOG_DIR/"
echo "====================================================="
echo "Press Ctrl+C to stop both."
echo ""

tail -f "$BACKEND_LOG" "$FRONTEND_LOG"
