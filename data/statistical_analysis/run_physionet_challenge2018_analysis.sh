#!/usr/bin/env bash
set -euo pipefail

# Dataset-specific wrapper for the generic EEG statistical analysis launcher.
# Defaults target the H100 shared dataset layout, but every path can be
# overridden with environment variables.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SHARED_EEG_ROOT="${SHARED_EEG_ROOT:-/mnt/ddn/shared/datasets/eeg}"
EEG_FM_ROOT="${EEG_FM_ROOT:-${SHARED_EEG_ROOT}/eeg_fm}"

PYTHON_BIN="${PYTHON_BIN:-${EEG_FM_ROOT}/venv/bin/python}"
DATASET_NAME="${DATASET_NAME:-PhysioNet_Challenge2018}"
INPUT_ROOT="${INPUT_ROOT:-${SHARED_EEG_ROOT}/PhysioNet/challenge-2018/1.0.0}"
OUTPUT_ROOT="${OUTPUT_ROOT:-${SHARED_EEG_ROOT}/statistical_reports}"
WORKERS="${WORKERS:-8}"
RAW_FORMATS="${RAW_FORMATS:-hea}"
MAX_FILES="${MAX_FILES:-0}"
FOLLOW_SYMLINKS="${FOLLOW_SYMLINKS:-false}"
DEEP_SIGNAL_SCAN="${DEEP_SIGNAL_SCAN:-false}"
OVERWRITE="${OVERWRITE:-false}"

exec bash "${SCRIPT_DIR}/run_statistical_analysis.sh" \
  --python-bin "${PYTHON_BIN}" \
  --dataset-name "${DATASET_NAME}" \
  --input-root "${INPUT_ROOT}" \
  --output-root "${OUTPUT_ROOT}" \
  --workers "${WORKERS}" \
  --raw-formats "${RAW_FORMATS}" \
  --max-files "${MAX_FILES}" \
  --follow-symlinks "${FOLLOW_SYMLINKS}" \
  --deep-signal-scan "${DEEP_SIGNAL_SCAN}" \
  --overwrite "${OVERWRITE}"
