#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PLANB_RUNTIME_DIR="${PLANB_RUNTIME_DIR:-$PWD/.openneuro_planb_runtime}"
PLANB_VENV_DIR="${PLANB_VENV_DIR:-$PLANB_RUNTIME_DIR/venv}"
PLANB_INSTALL_AWSCLI="${PLANB_INSTALL_AWSCLI:-true}"

OUTPUT_DIR="${OUTPUT_DIR:-$PWD/openneuro_planb_stage}"
STATE_DIR="${STATE_DIR:-$PWD/openneuro_planb_state}"
LOG_DIR="${LOG_DIR:-$PWD/openneuro_planb_logs}"
UPLOAD_COMMAND="${UPLOAD_COMMAND:-}"
PLANB_CONTINUOUS="${PLANB_CONTINUOUS:-true}"
PLANB_MAX_BATCHES="${PLANB_MAX_BATCHES:-0}"
PLANB_LOCAL_BUDGET_GB="${PLANB_LOCAL_BUDGET_GB:-250}"
PLANB_BATCH_TARGET_GB="${PLANB_BATCH_TARGET_GB:-220}"
PLANB_MIN_FREE_GB="${PLANB_MIN_FREE_GB:-20}"
PLANB_MAX_WORKERS="${PLANB_MAX_WORKERS:-4}"
PLANB_TRANSFER_BACKEND="${PLANB_TRANSFER_BACKEND:-auto}"
PLANB_BACKEND_PROBE_MB="${PLANB_BACKEND_PROBE_MB:-64}"
PLANB_OBJECT_CHUNK_MB="${PLANB_OBJECT_CHUNK_MB:-512}"
PLANB_RETRIES="${PLANB_RETRIES:-5}"

for arg in "$@"; do
  if [[ "$arg" == "--no-install" || "$arg" == "--help" || "$arg" == "-h" ]]; then
    PLANB_INSTALL_AWSCLI="false"
  fi
done
case "${1:-download}" in
  status|mark-uploaded)
    PLANB_INSTALL_AWSCLI="false"
    ;;
esac

"$PYTHON_BIN" - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit(f"Python >= 3.10 required, got {sys.version.split()[0]}")
print(f"Python {sys.version.split()[0]} OK")
PY

mkdir -p "$OUTPUT_DIR" "$STATE_DIR" "$LOG_DIR"

LOCK_FILE="$STATE_DIR/openneuro_planb.lock"
LOCK_DIR="$STATE_DIR/openneuro_planb.lockdir"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "[LOCK] another OpenNeuro PlanB run is already active for STATE_DIR=$STATE_DIR"
    exit 9
  fi
else
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    echo "[LOCK] another OpenNeuro PlanB run is already active for STATE_DIR=$STATE_DIR"
    exit 9
  fi
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT
fi

have_awscli() {
  command -v aws >/dev/null 2>&1 || "$PYTHON_BIN" -m awscli --version >/dev/null 2>&1
}

try_install_awscli() {
  if [[ "$PLANB_INSTALL_AWSCLI" == "0" || "$PLANB_INSTALL_AWSCLI" == "false" || "$PLANB_INSTALL_AWSCLI" == "no" ]]; then
    echo "[DEPS] PLANB_INSTALL_AWSCLI=$PLANB_INSTALL_AWSCLI; skipping awscli install"
    return 0
  fi
  if have_awscli; then
    echo "[DEPS] awscli already available"
    return 0
  fi

  mkdir -p "$PLANB_RUNTIME_DIR"
  echo "[DEPS] awscli not found; trying local venv install under $PLANB_VENV_DIR"
  if "$PYTHON_BIN" -m venv "$PLANB_VENV_DIR" >/dev/null 2>&1; then
    PYTHON_BIN="$PLANB_VENV_DIR/bin/python"
    export PATH="$PLANB_VENV_DIR/bin:$PATH"
    if "$PYTHON_BIN" -m pip install --upgrade pip awscli; then
      echo "[DEPS] awscli installed in $PLANB_VENV_DIR"
      return 0
    fi
    echo "[DEPS] venv awscli install failed; continuing with urllib/curl fallback"
  else
    echo "[DEPS] python venv creation failed; trying --user pip install"
  fi

  if "$PYTHON_BIN" -m pip install --user --upgrade awscli; then
    export PATH="$HOME/.local/bin:$PATH"
    echo "[DEPS] awscli installed with --user pip"
  else
    echo "[DEPS] awscli install failed; continuing with urllib/curl fallback"
  fi
}

try_install_awscli

SUBCOMMAND="${1:-download}"
case "$SUBCOMMAND" in
  download|speed-test|status|mark-uploaded)
    shift || true
    ;;
  *)
    SUBCOMMAND="download"
    ;;
esac

has_arg() {
  local needle="$1"
  shift || true
  for arg in "$@"; do
    if [[ "$arg" == "$needle" || "$arg" == "$needle="* ]]; then
      return 0
    fi
  done
  return 1
}

is_truthy() {
  case "$1" in
    1|true|TRUE|yes|YES|y|Y) return 0 ;;
    *) return 1 ;;
  esac
}

if [[ "$SUBCOMMAND" == "download" ]]; then
  EXTRA_ARGS=()
  if ! has_arg "--local-budget-gb" "$@"; then
    EXTRA_ARGS+=(--local-budget-gb "$PLANB_LOCAL_BUDGET_GB")
  fi
  if ! has_arg "--batch-target-gb" "$@"; then
    EXTRA_ARGS+=(--batch-target-gb "$PLANB_BATCH_TARGET_GB")
  fi
  if ! has_arg "--min-free-gb" "$@"; then
    EXTRA_ARGS+=(--min-free-gb "$PLANB_MIN_FREE_GB")
  fi
  if ! has_arg "--max-workers" "$@"; then
    EXTRA_ARGS+=(--max-workers "$PLANB_MAX_WORKERS")
  fi
  if ! has_arg "--transfer-backend" "$@"; then
    EXTRA_ARGS+=(--transfer-backend "$PLANB_TRANSFER_BACKEND")
  fi
  if ! has_arg "--backend-probe-mb" "$@"; then
    EXTRA_ARGS+=(--backend-probe-mb "$PLANB_BACKEND_PROBE_MB")
  fi
  if ! has_arg "--object-chunk-mb" "$@"; then
    EXTRA_ARGS+=(--object-chunk-mb "$PLANB_OBJECT_CHUNK_MB")
  fi
  if ! has_arg "--retries" "$@"; then
    EXTRA_ARGS+=(--retries "$PLANB_RETRIES")
  fi
  if ! has_arg "--max-batches" "$@"; then
    EXTRA_ARGS+=(--max-batches "$PLANB_MAX_BATCHES")
  fi
  if [[ -n "$UPLOAD_COMMAND" ]] && ! has_arg "--upload-command" "$@"; then
    EXTRA_ARGS+=(--upload-command "$UPLOAD_COMMAND")
  fi
  if is_truthy "$PLANB_CONTINUOUS" && [[ -z "$UPLOAD_COMMAND" ]] && ! has_arg "--upload-command" "$@" && ! has_arg "--dry-run" "$@"; then
    echo "[ERROR] PLANB_CONTINUOUS=true requires UPLOAD_COMMAND or --upload-command."
    echo "        Set PLANB_CONTINUOUS=false for a one-batch local staging run."
    exit 8
  fi

  echo "[RUN] output_dir=$OUTPUT_DIR"
  echo "[RUN] state_dir=$STATE_DIR"
  echo "[RUN] log_dir=$LOG_DIR"
  echo "[RUN] continuous=$PLANB_CONTINUOUS max_batches=${PLANB_MAX_BATCHES}"
  echo "[RUN] backend=$PLANB_TRANSFER_BACKEND workers=$PLANB_MAX_WORKERS batch_target_gb=$PLANB_BATCH_TARGET_GB"

  exec "$PYTHON_BIN" "$SCRIPT_DIR/download_OpenNeuro_planb.py" \
    download \
    --output-dir "$OUTPUT_DIR" \
    --state-dir "$STATE_DIR" \
    --log-dir "$LOG_DIR" \
    "${EXTRA_ARGS[@]}" \
    "$@"
else
  exec "$PYTHON_BIN" "$SCRIPT_DIR/download_OpenNeuro_planb.py" \
    "$SUBCOMMAND" \
    --state-dir "$STATE_DIR" \
    --log-dir "$LOG_DIR" \
    "$@"
fi
