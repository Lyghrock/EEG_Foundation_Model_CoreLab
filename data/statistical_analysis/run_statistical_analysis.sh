#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON_BIN="${PYTHON_BIN:-python3}"

DATASET_NAME=""
INPUT_ROOT=""
OUTPUT_ROOT=""
OUTPUT_DIR=""
WORKERS="4"
RAW_FORMATS="edf,bdf,gdf,vhdr,set,fif,fif.gz,cnt,mff,hea"
FOLLOW_SYMLINKS="false"
DEEP_SIGNAL_SCAN="false"
MAX_FILES="0"
OVERWRITE="false"

usage() {
  cat <<'USAGE'
Usage:
  bash data/statistical_analysis/run_statistical_analysis.sh \
    --dataset-name TUH \
    --input-root /media/yizan/nevermind/tuh_eeg \
    --output-root /home/yizan/TUH_Download/statistical_reports

Required:
  --dataset-name NAME       Logical dataset name used in output paths.
  --input-root PATH         Dataset directory to scan; must be explicit.
  --output-root PATH        Parent directory for timestamped analysis output.

Optional:
  --python-bin PATH        Python executable to run analysis scripts. Default: python3 or PYTHON_BIN.
  --output-dir PATH         Exact output directory. Overrides --output-root timestamping.
  --workers N              Parallel raw EEG metadata readers. Default: 4.
  --raw-formats LIST       Comma-separated primary raw formats. Default: edf,bdf,gdf,vhdr,set,fif,fif.gz,cnt,mff,hea.
  --follow-symlinks BOOL   true/false. Default: false.
  --deep-signal-scan BOOL  Reserved for future payload scan. Default: false.
  --max-files N            Smoke-test limit. 0 means all files.
  --overwrite BOOL         Allow existing --output-dir. Default: false.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dataset-name) DATASET_NAME="$2"; shift 2 ;;
    --input-root) INPUT_ROOT="$2"; shift 2 ;;
    --output-root) OUTPUT_ROOT="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --python-bin) PYTHON_BIN="$2"; shift 2 ;;
    --workers) WORKERS="$2"; shift 2 ;;
    --raw-formats) RAW_FORMATS="$2"; shift 2 ;;
    --follow-symlinks) FOLLOW_SYMLINKS="$2"; shift 2 ;;
    --deep-signal-scan) DEEP_SIGNAL_SCAN="$2"; shift 2 ;;
    --max-files) MAX_FILES="$2"; shift 2 ;;
    --overwrite) OVERWRITE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "[ERROR] Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$DATASET_NAME" || -z "$INPUT_ROOT" || -z "$OUTPUT_ROOT$OUTPUT_DIR" ]]; then
  echo "[ERROR] --dataset-name, --input-root, and --output-root or --output-dir are required." >&2
  usage
  exit 2
fi
if [[ ! -d "$INPUT_ROOT" ]]; then
  echo "[ERROR] Input root does not exist: $INPUT_ROOT" >&2
  exit 2
fi
if [[ "$PYTHON_BIN" == */* && ! -x "$PYTHON_BIN" ]]; then
  echo "[ERROR] Python executable is not executable: $PYTHON_BIN" >&2
  exit 2
fi

INPUT_ROOT="$(cd "$INPUT_ROOT" && pwd -P)"
if [[ -z "$OUTPUT_DIR" ]]; then
  mkdir -p "$OUTPUT_ROOT"
  OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd -P)"
  STAMP="$(date +%Y%m%d_%H%M%S)"
  OUTPUT_DIR="$OUTPUT_ROOT/${DATASET_NAME}_${STAMP}"
fi

if [[ -e "$OUTPUT_DIR" && "$OVERWRITE" != "true" ]]; then
  echo "[ERROR] Output directory already exists: $OUTPUT_DIR" >&2
  echo "        Re-run with --overwrite true or choose a different --output-dir." >&2
  exit 2
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"

RUN_CONFIG="$OUTPUT_DIR/run_config.json"
cat > "$RUN_CONFIG" <<JSON
{
  "dataset_name": "$DATASET_NAME",
  "input_root": "$INPUT_ROOT",
  "output_dir": "$OUTPUT_DIR",
  "workers": $WORKERS,
  "raw_formats": "$RAW_FORMATS",
  "follow_symlinks": "$FOLLOW_SYMLINKS",
  "deep_signal_scan": "$DEEP_SIGNAL_SCAN",
  "max_files": $MAX_FILES,
  "python_bin": "$PYTHON_BIN"
}
JSON

echo "=========================================="
echo "EEG dataset statistical analysis"
echo "Dataset name: $DATASET_NAME"
echo "Input root:   $INPUT_ROOT"
echo "Output dir:   $OUTPUT_DIR"
echo "Python:       $PYTHON_BIN"
echo "Workers:      $WORKERS"
echo "Raw formats:  $RAW_FORMATS"
echo "Max files:    $MAX_FILES"
echo "=========================================="

FOLLOW_ARGS=()
if [[ "$FOLLOW_SYMLINKS" == "true" ]]; then
  FOLLOW_ARGS+=(--follow-symlinks)
fi
DEEP_ARGS=()
if [[ "$DEEP_SIGNAL_SCAN" == "true" ]]; then
  DEEP_ARGS+=(--deep-signal-scan)
fi
MAX_ARGS=()
if [[ "$MAX_FILES" != "0" ]]; then
  MAX_ARGS+=(--max-files "$MAX_FILES")
fi

"$PYTHON_BIN" "$SCRIPT_DIR/analyze_inventory.py" \
  --dataset-name "$DATASET_NAME" \
  --input-root "$INPUT_ROOT" \
  --output-dir "$OUTPUT_DIR" \
  --raw-formats "$RAW_FORMATS" \
  "${FOLLOW_ARGS[@]}" \
  "${MAX_ARGS[@]}"

"$PYTHON_BIN" "$SCRIPT_DIR/analyze_raw_eeg.py" \
  --dataset-name "$DATASET_NAME" \
  --inventory-csv "$OUTPUT_DIR/dataset_inventory.csv" \
  --input-root "$INPUT_ROOT" \
  --output-dir "$OUTPUT_DIR" \
  --raw-formats "$RAW_FORMATS" \
  --workers "$WORKERS" \
  "${DEEP_ARGS[@]}" \
  "${MAX_ARGS[@]}"

"$PYTHON_BIN" "$SCRIPT_DIR/compute_statistics.py" \
  --dataset-name "$DATASET_NAME" \
  --output-dir "$OUTPUT_DIR"

"$PYTHON_BIN" "$SCRIPT_DIR/plot_statistics.py" \
  --output-dir "$OUTPUT_DIR"

echo "=========================================="
echo "Analysis complete"
echo "Output dir: $OUTPUT_DIR"
echo "Key files:"
echo "  $OUTPUT_DIR/dataset_inventory_summary.json"
echo "  $OUTPUT_DIR/raw_eeg_index.csv"
echo "  $OUTPUT_DIR/pair_summary.json"
echo "  $OUTPUT_DIR/statistics_summary.json"
echo "  $OUTPUT_DIR/stats_tables/"
echo "  $OUTPUT_DIR/plot_data/"
echo "  $OUTPUT_DIR/plots/"
echo "=========================================="
