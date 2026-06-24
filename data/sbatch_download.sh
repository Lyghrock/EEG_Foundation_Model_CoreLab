#!/bin/bash
# =============================================================================
# sbatch_download.sh - Generic Slurm launcher for EEG dataset downloads
#
# Usage examples:
#   sbatch sbatch_download.sh
#   sbatch sbatch_download.sh --dry-run
#   DATA_SOURCE=physionet sbatch sbatch_download.sh
#   DATA_SOURCE=openneuro MAX_SIZE_MB=102400 sbatch sbatch_download.sh --dataset ds002778
#
# Notes:
#   - This launcher is intended for H100-style Slurm download jobs.
#   - It does not pull/sync code. Put the target Python scripts in place first.
#   - Edit the absolute paths in the configuration block before using another
#     account or another machine.
#   - Slurm #SBATCH log directives cannot use shell variables. Runtime logs are
#     mirrored to LOG_DIR after the job starts.
# =============================================================================

#SBATCH --job-name=eeg_download
#SBATCH --output=slurm-%x-%j.out
#SBATCH --error=slurm-%x-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=72:00:00
#SBATCH --gres=none

set -euo pipefail

LAUNCHER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
SHARED_EEG_ROOT="${SHARED_EEG_ROOT:-/mnt/ddn/shared/datasets/eeg}"

# =============================================================================
# User configuration - use absolute paths for H100 or other shared accounts.
# Values can also be overridden at submission time, for example:
#   DATA_SOURCE=physionet OUTPUT_DIR=/abs/data/path sbatch sbatch_download.sh
# =============================================================================

# Dataset source selector. Supported: openneuro, physionet, custom.
DATA_SOURCE="${DATA_SOURCE:-openneuro}"

case "${DATA_SOURCE}" in
    openneuro)
        DEFAULT_OUTPUT_DIR="/mnt/ddn/shared/datasets/eeg/OpenNeuro"
        ;;
    physionet)
        DEFAULT_OUTPUT_DIR="/mnt/ddn/shared/datasets/eeg/PhysioNet"
        ;;
    custom)
        DEFAULT_OUTPUT_DIR="/mnt/ddn/shared/datasets/eeg/custom"
        ;;
    *)
        DEFAULT_OUTPUT_DIR="/mnt/ddn/shared/datasets/eeg/${DATA_SOURCE}"
        ;;
esac

# Absolute code paths. By default, keep this launcher and download_*.py files in
# the same directory so the whole download bundle can live outside /home/weijun.
DOWNLOAD_SCRIPT_DIR="${DOWNLOAD_SCRIPT_DIR:-${LAUNCHER_DIR}}"
REPO_DIR="${REPO_DIR:-${DOWNLOAD_SCRIPT_DIR}}"
OPENNEURO_SCRIPT="${OPENNEURO_SCRIPT:-${DOWNLOAD_SCRIPT_DIR}/download_OpenNeuro.py}"
PHYSIONET_SCRIPT="${PHYSIONET_SCRIPT:-${DOWNLOAD_SCRIPT_DIR}/download_PhysioNet.py}"
CUSTOM_DOWNLOAD_SCRIPT="${CUSTOM_DOWNLOAD_SCRIPT:-}"

# Absolute storage/log paths.
OUTPUT_DIR="${OUTPUT_DIR:-${DEFAULT_OUTPUT_DIR}}"
LOG_DIR="${LOG_DIR:-${SHARED_EEG_ROOT}/logs/download}"

# Runtime environment. Set CONDA_ENV="" to skip conda activation.
CONDA_SH="${CONDA_SH:-}"
SHARED_ENV_PYTHON="${SHARED_ENV_PYTHON:-${SHARED_EEG_ROOT}/envs/eeg_fm/bin/python}"
if [ -z "${PYTHON_BIN+x}" ] && [ -x "${SHARED_ENV_PYTHON}" ]; then
    PYTHON_BIN="${SHARED_ENV_PYTHON}"
    CONDA_ENV="${CONDA_ENV-}"
else
    PYTHON_BIN="${PYTHON_BIN:-python3}"
    CONDA_ENV="${CONDA_ENV-eeg_fm}"
fi
CHECK_IMPORTS="${CHECK_IMPORTS:-true}"
export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"

# Download controls shared by download_OpenNeuro.py and planned future scripts.
MAX_SIZE_MB="${MAX_SIZE_MB:-0}"          # 0 means unlimited when supported.
MAX_WORKERS="${MAX_WORKERS:-${SLURM_CPUS_PER_TASK:-8}}"
DRY_RUN="${DRY_RUN:-false}"

# Optional preprocessing flags. Disable for pure download jobs.
ENABLE_PREPROCESS="${ENABLE_PREPROCESS:-false}"
TARGET_FS="${TARGET_FS:-250}"
ALIGN_SFREQ="${ALIGN_SFREQ:-true}"
STANDARD_CHANNELS="${STANDARD_CHANNELS:-}"
TARGET_DURATION="${TARGET_DURATION:-}"
LENGTH_MODE="${LENGTH_MODE:-crop}"
INTERPOLATE_CHANNELS="${INTERPOLATE_CHANNELS:-false}"
REMOVE_ORIGINAL="${REMOVE_ORIGINAL:-true}"

# =============================================================================

is_true() {
    case "$1" in
        true|TRUE|True|1|yes|YES|Yes|y|Y) return 0 ;;
        *) return 1 ;;
    esac
}

require_abs_path() {
    local value="$1"
    local name="$2"
    if [ -z "${value}" ]; then
        echo "[ERROR] ${name} is empty"
        exit 1
    fi
    case "${value}" in
        /*) ;;
        *)
            echo "[ERROR] ${name} must be an absolute path: ${value}"
            exit 1
            ;;
    esac
}

select_download_script() {
    case "${DATA_SOURCE}" in
        openneuro)
            DOWNLOAD_SCRIPT="${OPENNEURO_SCRIPT}"
            REQUIRED_IMPORTS="import mne, openneuro, requests"
            ;;
        physionet)
            DOWNLOAD_SCRIPT="${PHYSIONET_SCRIPT}"
            REQUIRED_IMPORTS="import requests"
            ;;
        custom)
            DOWNLOAD_SCRIPT="${CUSTOM_DOWNLOAD_SCRIPT}"
            REQUIRED_IMPORTS=""
            ;;
        *)
            echo "[ERROR] Unsupported DATA_SOURCE: ${DATA_SOURCE}"
            echo "Supported values: openneuro, physionet, custom"
            exit 1
            ;;
    esac
}

activate_conda() {
    if [ -z "${CONDA_ENV}" ]; then
        echo "[INFO] CONDA_ENV is empty; skipping conda activation"
        return
    fi

    if [ -n "${CONDA_SH}" ]; then
        require_abs_path "${CONDA_SH}" "CONDA_SH"
        if [ ! -f "${CONDA_SH}" ]; then
            echo "[ERROR] CONDA_SH does not exist: ${CONDA_SH}"
            exit 1
        fi
        source "${CONDA_SH}"
        conda activate "${CONDA_ENV}"
        return
    fi

    if command -v conda >/dev/null 2>&1; then
        eval "$(conda shell.bash hook)"
        conda activate "${CONDA_ENV}"
        return
    fi

    echo "[ERROR] Cannot activate conda env: ${CONDA_ENV}"
    echo "Set CONDA_SH to an absolute conda.sh path, or set CONDA_ENV=\"\" to skip."
    exit 1
}

print_command() {
    printf "Running:"
    printf " %q" "$@"
    echo
}

select_download_script

require_abs_path "${REPO_DIR}" "REPO_DIR"
require_abs_path "${DOWNLOAD_SCRIPT}" "DOWNLOAD_SCRIPT"
require_abs_path "${OUTPUT_DIR}" "OUTPUT_DIR"
require_abs_path "${LOG_DIR}" "LOG_DIR"

if [ ! -f "${DOWNLOAD_SCRIPT}" ]; then
    echo "[ERROR] Download script not found: ${DOWNLOAD_SCRIPT}"
    exit 1
fi

mkdir -p "${LOG_DIR}"
RUN_ID="${SLURM_JOB_ID:-manual_$(date +%Y%m%d_%H%M%S)}"
LOG_FILE="${LOG_DIR}/${DATA_SOURCE}_${RUN_ID}.log"
exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=========================================="
echo "Job started at: $(date)"
echo "Host: $(hostname)"
echo "SLURM Job ID: ${SLURM_JOB_ID:-none}"
echo "Data source: ${DATA_SOURCE}"
echo "Download script: ${DOWNLOAD_SCRIPT}"
echo "Output dir: ${OUTPUT_DIR}"
echo "Log file: ${LOG_FILE}"
echo "CPU cores: ${SLURM_CPUS_PER_TASK:-${MAX_WORKERS}}"
echo "Max workers: ${MAX_WORKERS}"
echo "Max size: ${MAX_SIZE_MB} MB"
echo "Preprocess: ${ENABLE_PREPROCESS}"
echo "=========================================="
echo ""

activate_conda

echo "Using Python: $(command -v "${PYTHON_BIN}" || echo "${PYTHON_BIN}")"
if is_true "${CHECK_IMPORTS}" && [ -n "${REQUIRED_IMPORTS}" ]; then
    "${PYTHON_BIN}" -c "${REQUIRED_IMPORTS}; print('Required imports OK')"
fi
echo ""

mkdir -p "${OUTPUT_DIR}"

CMD=(
    "${PYTHON_BIN}"
    "${DOWNLOAD_SCRIPT}"
    "--output-dir" "${OUTPUT_DIR}"
    "--max-size-mb" "${MAX_SIZE_MB}"
    "--max-workers" "${MAX_WORKERS}"
)

if is_true "${DRY_RUN}"; then
    CMD+=("--dry-run")
fi

if is_true "${ENABLE_PREPROCESS}"; then
    CMD+=("--preprocess" "--target-fs" "${TARGET_FS}")

    if ! is_true "${ALIGN_SFREQ}"; then
        CMD+=("--no-align-sfreq")
    fi

    if [ -n "${STANDARD_CHANNELS}" ]; then
        CMD+=("--standard-channels" "${STANDARD_CHANNELS}")
        if is_true "${INTERPOLATE_CHANNELS}"; then
            CMD+=("--interpolate-channels")
        fi
    fi

    if [ -n "${TARGET_DURATION}" ]; then
        CMD+=("--target-duration" "${TARGET_DURATION}" "--length-mode" "${LENGTH_MODE}")
    fi

    if ! is_true "${REMOVE_ORIGINAL}"; then
        CMD+=("--no-remove-original")
    fi
fi

if [ "$#" -gt 0 ]; then
    CMD+=("$@")
fi

print_command "${CMD[@]}"
echo "=========================================="
echo ""

cd "$(dirname "${DOWNLOAD_SCRIPT}")"
"${CMD[@]}"
EXIT_CODE=$?

echo ""
echo "=========================================="
echo "Job finished at: $(date)"
echo "Exit code: ${EXIT_CODE}"
echo "=========================================="

exit "${EXIT_CODE}"
