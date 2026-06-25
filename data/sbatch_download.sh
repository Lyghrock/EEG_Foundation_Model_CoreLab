#!/bin/bash
# =============================================================================
# sbatch_download.sh - Generic Slurm launcher for EEG dataset downloads
#
# Usage examples:
#   sbatch sbatch_download.sh
#   sbatch sbatch_download.sh --dry-run
#   sbatch sbatch_download.sh --data-source physionet --discover --sort size
#   sbatch sbatch_download.sh --data-source openneuro --max-size-mb 102400 --dataset ds002778
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
#SBATCH --partition=h100
#SBATCH --cpus-per-task=16
#SBATCH --mem=32G
#SBATCH --time=72:00:00
#SBATCH --gres=gpu:1

set -euo pipefail

SHARED_EEG_ROOT="${SHARED_EEG_ROOT:-/mnt/ddn/shared/datasets/eeg}"
EEG_FM_ROOT="${EEG_FM_ROOT:-${SHARED_EEG_ROOT}/eeg_fm}"

resolve_script_dir() {
    # Slurm copies the batch script into its spool directory before execution,
    # so BASH_SOURCE[0] can point at /cm/.../spool/job*/ instead of the submit
    # directory. Prefer the actual working directory first because sbatch
    # --chdir changes PWD but not SLURM_SUBMIT_DIR.
    if ls "$(pwd -P)"/download_*.py >/dev/null 2>&1; then
        pwd -P
        return
    fi

    # If the user submitted from the data directory without --chdir, Slurm also
    # records that location here.
    if [ -n "${SLURM_SUBMIT_DIR:-}" ] && ls "${SLURM_SUBMIT_DIR}"/download_*.py >/dev/null 2>&1; then
        cd "${SLURM_SUBMIT_DIR}" && pwd -P
        return
    fi

    cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P
}

LAUNCHER_DIR="$(resolve_script_dir)"

# =============================================================================
# User configuration - use absolute paths for H100 or other shared accounts.
# Values can also be overridden at submission time, for example:
#   DATA_SOURCE=physionet OUTPUT_DIR=/abs/data/path sbatch sbatch_download.sh
# =============================================================================

# Dataset source selector. Supported: openneuro, physionet, custom.
DATA_SOURCE="${DATA_SOURCE:-openneuro}"
PREV_ARG=""
for ARG in "$@"; do
    if [ "${PREV_ARG}" = "--data-source" ] || [ "${PREV_ARG}" = "--source" ]; then
        DATA_SOURCE="${ARG}"
        PREV_ARG=""
        continue
    fi
    case "${ARG}" in
        --data-source=*|--source=*) DATA_SOURCE="${ARG#*=}" ;;
        --data-source|--source) PREV_ARG="${ARG}" ;;
        *) PREV_ARG="" ;;
    esac
done

case "${DATA_SOURCE}" in
    openneuro)
        DEFAULT_OUTPUT_DIR="${EEG_FM_ROOT}/OpenNeuro"
        ;;
    physionet)
        DEFAULT_OUTPUT_DIR="${EEG_FM_ROOT}/PhysioNet"
        ;;
    custom)
        DEFAULT_OUTPUT_DIR="${EEG_FM_ROOT}/custom"
        ;;
    *)
        DEFAULT_OUTPUT_DIR="${EEG_FM_ROOT}/${DATA_SOURCE}"
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
LOG_DIR="${LOG_DIR:-${EEG_FM_ROOT}/logs/download}"

# Runtime environment. This launcher does not use conda by default because H100
# download jobs run under a shared account without interactive conda setup.
CONDA_SH="${CONDA_SH:-}"
SHARED_ENV_PYTHON="${SHARED_ENV_PYTHON:-${EEG_FM_ROOT}/venv/bin/python}"
LEGACY_SHARED_ENV_PYTHON="${LEGACY_SHARED_ENV_PYTHON:-${SHARED_EEG_ROOT}/envs/eeg_fm/bin/python}"
if [ -z "${PYTHON_BIN+x}" ] && [ -x "${SHARED_ENV_PYTHON}" ]; then
    PYTHON_BIN="${SHARED_ENV_PYTHON}"
elif [ -z "${PYTHON_BIN+x}" ] && [ -x "${LEGACY_SHARED_ENV_PYTHON}" ]; then
    PYTHON_BIN="${LEGACY_SHARED_ENV_PYTHON}"
else
    PYTHON_BIN="${PYTHON_BIN:-python3}"
fi
CONDA_ENV="${CONDA_ENV:-}"
CHECK_IMPORTS="${CHECK_IMPORTS:-true}"
export PYTHONNOUSERSITE="${PYTHONNOUSERSITE:-1}"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

# Download controls shared by download_OpenNeuro.py and planned future scripts.
MAX_SIZE_MB="${MAX_SIZE_MB:-0}"          # 0 means unlimited when supported.
MAX_WORKERS="${MAX_WORKERS:-4}"
DRY_RUN="${DRY_RUN:-false}"
OPENNEURO_BACKEND="${OPENNEURO_BACKEND:-aws}"  # auto, aws, openneuro

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

DOWNLOAD_ARGS=()

launcher_usage() {
    cat <<USAGE
Usage:
  sbatch sbatch_download.sh [launcher options] [downloader options]

Launcher options:
  --data-source openneuro|physionet|custom
  --output-dir /abs/path
  --log-dir /abs/path
  --python-bin /abs/path/python
  --download-script-dir /abs/path/to/repo/data
  --max-workers N
  --max-size-mb N
  --dry-run
  --openneuro-backend auto|aws|openneuro
  --download-backend auto|aws|openneuro

Examples:
  sbatch sbatch_download.sh --data-source openneuro --openneuro-backend aws --max-workers 2
  sbatch sbatch_download.sh --data-source physionet --max-workers 4 --discover --sort size
USAGE
}

parse_launcher_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --launcher-help)
                launcher_usage
                exit 0
                ;;
            --)
                shift
                DOWNLOAD_ARGS+=("$@")
                break
                ;;
            --data-source|--source)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                DATA_SOURCE="$2"
                shift 2
                ;;
            --data-source=*|--source=*)
                DATA_SOURCE="${1#*=}"
                shift
                ;;
            --output-dir)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                OUTPUT_DIR="$2"
                shift 2
                ;;
            --output-dir=*)
                OUTPUT_DIR="${1#*=}"
                shift
                ;;
            --log-dir)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                LOG_DIR="$2"
                shift 2
                ;;
            --log-dir=*)
                LOG_DIR="${1#*=}"
                shift
                ;;
            --python-bin)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                PYTHON_BIN="$2"
                shift 2
                ;;
            --python-bin=*)
                PYTHON_BIN="${1#*=}"
                shift
                ;;
            --download-script-dir)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                DOWNLOAD_SCRIPT_DIR="$2"
                REPO_DIR="${DOWNLOAD_SCRIPT_DIR}"
                OPENNEURO_SCRIPT="${DOWNLOAD_SCRIPT_DIR}/download_OpenNeuro.py"
                PHYSIONET_SCRIPT="${DOWNLOAD_SCRIPT_DIR}/download_PhysioNet.py"
                shift 2
                ;;
            --download-script-dir=*)
                DOWNLOAD_SCRIPT_DIR="${1#*=}"
                REPO_DIR="${DOWNLOAD_SCRIPT_DIR}"
                OPENNEURO_SCRIPT="${DOWNLOAD_SCRIPT_DIR}/download_OpenNeuro.py"
                PHYSIONET_SCRIPT="${DOWNLOAD_SCRIPT_DIR}/download_PhysioNet.py"
                shift
                ;;
            --max-workers)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                MAX_WORKERS="$2"
                shift 2
                ;;
            --max-workers=*)
                MAX_WORKERS="${1#*=}"
                shift
                ;;
            --max-size-mb)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                MAX_SIZE_MB="$2"
                shift 2
                ;;
            --max-size-mb=*)
                MAX_SIZE_MB="${1#*=}"
                shift
                ;;
            --dry-run|--dryrun)
                DRY_RUN=true
                shift
                ;;
            --no-dry-run)
                DRY_RUN=false
                shift
                ;;
            --openneuro-backend|--download-backend)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                OPENNEURO_BACKEND="$2"
                shift 2
                ;;
            --openneuro-backend=*|--download-backend=*)
                OPENNEURO_BACKEND="${1#*=}"
                shift
                ;;
            --preprocess)
                ENABLE_PREPROCESS=true
                shift
                ;;
            --no-preprocess)
                ENABLE_PREPROCESS=false
                shift
                ;;
            --target-fs)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                TARGET_FS="$2"
                shift 2
                ;;
            --target-fs=*)
                TARGET_FS="${1#*=}"
                shift
                ;;
            --target-duration)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                TARGET_DURATION="$2"
                shift 2
                ;;
            --target-duration=*)
                TARGET_DURATION="${1#*=}"
                shift
                ;;
            --length-mode)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                LENGTH_MODE="$2"
                shift 2
                ;;
            --length-mode=*)
                LENGTH_MODE="${1#*=}"
                shift
                ;;
            --standard-channels)
                [ "$#" -ge 2 ] || { echo "[ERROR] $1 requires a value"; exit 1; }
                STANDARD_CHANNELS="$2"
                shift 2
                ;;
            --standard-channels=*)
                STANDARD_CHANNELS="${1#*=}"
                shift
                ;;
            --interpolate-channels)
                INTERPOLATE_CHANNELS=true
                shift
                ;;
            --no-interpolate-channels)
                INTERPOLATE_CHANNELS=false
                shift
                ;;
            --remove-original)
                REMOVE_ORIGINAL=true
                shift
                ;;
            --no-remove-original)
                REMOVE_ORIGINAL=false
                shift
                ;;
            *)
                DOWNLOAD_ARGS+=("$1")
                shift
                ;;
        esac
    done
}

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

parse_launcher_args "$@"
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
echo "OpenNeuro backend: ${OPENNEURO_BACKEND}"
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

if [ "${DATA_SOURCE}" = "openneuro" ]; then
    CMD+=("--download-backend" "${OPENNEURO_BACKEND}")
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

if [ "${#DOWNLOAD_ARGS[@]}" -gt 0 ]; then
    CMD+=("${DOWNLOAD_ARGS[@]}")
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
