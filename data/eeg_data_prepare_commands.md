# EEG Data Prepare Command Collection

This file collects the current commands for PhysioNet discovery/download and
dataset statistical validation. H100 commands are split by account.

## 1. PhysioNet EEG Discovery Checks

Run on H100 with the shared venv, or on any machine with repo requirements
installed.

```bash
PY=/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python
REPO=/mnt/ddn/shared/datasets/eeg/eeg_fm/repo
OUT=/tmp/physionet_eeg_discovered.txt

cd "$REPO"
"$PY" data/download_PhysioNet.py \
  --discover \
  --dry-run \
  --sort name \
  --resolve-workers 4 \
  --write-eeg-list "$OUT"

grep -n '^challenge-2018/1.0.0' "$OUT"
grep -n '^siena-scalp-eeg/1.0.0' "$OUT"
```

Database-only comparison. This may miss Challenge 2018 because it is listed
under the challenge page:

```bash
PY=/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python
REPO=/mnt/ddn/shared/datasets/eeg/eeg_fm/repo
OUT=/tmp/physionet_eeg_database_only.txt

cd "$REPO"
"$PY" data/download_PhysioNet.py \
  --discover \
  --no-discover-challenges \
  --no-discover-topics \
  --no-include-known-eeg \
  --no-include-curated-seeds \
  --dry-run \
  --sort name \
  --resolve-workers 4 \
  --write-eeg-list "$OUT"

grep -n '^challenge-2018/1.0.0' "$OUT" || echo 'challenge-2018 missing from database-only discovery'
grep -n '^siena-scalp-eeg/1.0.0' "$OUT" || echo 'siena missing from database-only discovery'
```

Challenge-list-only check:

```bash
PY=/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python
REPO=/mnt/ddn/shared/datasets/eeg/eeg_fm/repo
OUT=/tmp/physionet_eeg_challenges_only.txt

cd "$REPO"
"$PY" data/download_PhysioNet.py \
  --discover \
  --no-discover-primary \
  --discover-challenges \
  --no-discover-topics \
  --no-include-known-eeg \
  --no-include-curated-seeds \
  --dry-run \
  --sort name \
  --resolve-workers 4 \
  --write-eeg-list "$OUT"

grep -n '^challenge-2018/1.0.0' "$OUT"
```

Focused discovery without the full database page:

```bash
PY=/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python
REPO=/mnt/ddn/shared/datasets/eeg/eeg_fm/repo
OUT=/tmp/physionet_eeg_focused.txt

cd "$REPO"
"$PY" data/download_PhysioNet.py \
  --discover \
  --no-discover-primary \
  --dry-run \
  --sort name \
  --resolve-workers 8 \
  --write-eeg-list "$OUT"

grep -n '^challenge-2018/1.0.0' "$OUT"
grep -n '^siena-scalp-eeg/1.0.0' "$OUT"
```

## 2. H100 Setup As `weijun`

Run this before `share` submits Slurm jobs.

```bash
set -euo pipefail

BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
DATA_ROOT=/mnt/ddn/shared/datasets/eeg
REPO=$BASE/repo
PY=$BASE/venv/bin/python

mkdir -p "$BASE" \
         "$BASE/OpenNeuro" \
         "$BASE/PhysioNet" \
         "$BASE/logs/slurm" \
         "$BASE/logs/download" \
         "$BASE/statistical_reports"

if [ -d "$REPO/.git" ]; then
  git -C "$REPO" checkout main
  git -C "$REPO" pull --ff-only origin main
else
  git clone git@github.com:Lyghrock/EEG_Foundation_Model_CoreLab.git "$REPO"
fi

if [ ! -x "$PY" ] || [ -L "$BASE/venv/bin/python3" ]; then
  rm -rf "$BASE/venv"
  /home/weijun/miniconda3/envs/eeg_fm/bin/python -m venv --copies "$BASE/venv"
fi

"$PY" -m pip install --upgrade pip
"$PY" -m pip install --no-cache-dir -r "$REPO/requirements.txt"

chmod -R a+rX "$REPO" "$BASE/venv"
chmod -R a+rwX "$BASE/OpenNeuro" "$BASE/PhysioNet" "$BASE/logs" "$BASE/statistical_reports" || \
  echo "[WARN] chmod had partial failures on files not owned by weijun"

if command -v setfacl >/dev/null 2>&1; then
  setfacl -R -m u:share:rX "$REPO" "$BASE/venv" || true
  setfacl -R -m u:share:rwX "$BASE/OpenNeuro" "$BASE/PhysioNet" "$BASE/logs" "$BASE/statistical_reports" || true
  setfacl -R -d -m u:share:rwX "$BASE/OpenNeuro" "$BASE/PhysioNet" "$BASE/logs" "$BASE/statistical_reports" || true
fi

test -r "$REPO/data/sbatch_download.sh"
test -r "$REPO/data/download_PhysioNet.py"
test -r "$REPO/data/download_OpenNeuro.py"
test -x "$PY"
test ! -L "$BASE/venv/bin/python3"
bash -n "$REPO/data/sbatch_download.sh"
bash -n "$REPO/data/statistical_analysis/run_openneuro_analysis.sh"
bash -n "$REPO/data/statistical_analysis/run_siena_analysis.sh"
bash -n "$REPO/data/statistical_analysis/run_physionet_challenge2018_analysis.sh"
"$PY" -c "import requests, numpy, pandas, matplotlib, mne; print('weijun checks OK')"
```

## 3. H100 Preflight As `share`

```bash
set -euo pipefail

BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
DATA_ROOT=/mnt/ddn/shared/datasets/eeg
REPO=$BASE/repo
PY=$BASE/venv/bin/python
DATA_DIR=$REPO/data
SLURM_LOG_DIR=$BASE/logs/slurm
DOWNLOAD_LOG_DIR=$BASE/logs/download
PHYSIONET_ROOT=$BASE/PhysioNet

cd "$DATA_DIR"

test "$(pwd -P)" = "$(cd "$DATA_DIR" && pwd -P)"
test -r sbatch_download.sh
test -r download_PhysioNet.py
test -r download_OpenNeuro.py
test -x "$PY"
bash -n sbatch_download.sh
bash -n "$REPO/data/statistical_analysis/run_openneuro_analysis.sh"
bash -n "$REPO/data/statistical_analysis/run_siena_analysis.sh"
bash -n "$REPO/data/statistical_analysis/run_physionet_challenge2018_analysis.sh"
"$PY" -c "import requests, numpy, pandas, matplotlib, mne; print('share imports OK')"

touch "$PHYSIONET_ROOT/.share_write_test" && rm -f "$PHYSIONET_ROOT/.share_write_test"
touch "$DOWNLOAD_LOG_DIR/.share_write_test" && rm -f "$DOWNLOAD_LOG_DIR/.share_write_test"
touch "$SLURM_LOG_DIR/.share_write_test" && rm -f "$SLURM_LOG_DIR/.share_write_test"
touch "$BASE/statistical_reports/.share_write_test" && rm -f "$BASE/statistical_reports/.share_write_test"
```

## 4. PhysioNet Downloads As `share`

Full discovery dry-run:

```bash
PY=/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python
REPO=/mnt/ddn/shared/datasets/eeg/eeg_fm/repo

cd "$REPO"
"$PY" data/download_PhysioNet.py \
  --discover \
  --dry-run \
  --sort size \
  --resolve-workers 4 \
  --write-eeg-list /tmp/physionet_eeg_discovered.txt
```

Slurm download for all discovered EEG-validated PhysioNet datasets:

```bash
set -euo pipefail

BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
DATA_ROOT=/mnt/ddn/shared/datasets/eeg
REPO=$BASE/repo
PY=$BASE/venv/bin/python
DATA_DIR=$REPO/data
SLURM_LOG_DIR=$BASE/logs/slurm
DOWNLOAD_LOG_DIR=$BASE/logs/download
PHYSIONET_ROOT=$BASE/PhysioNet

cd "$DATA_DIR"

JOB_RAW=$(sbatch --parsable \
  --chdir="$DATA_DIR" \
  --output="$SLURM_LOG_DIR/physionet-all-%j.out" \
  --error="$SLURM_LOG_DIR/physionet-all-%j.err" \
  sbatch_download.sh \
  --data-source physionet \
  --python-bin "$PY" \
  --download-script-dir "$DATA_DIR" \
  --output-dir "$PHYSIONET_ROOT" \
  --log-dir "$DOWNLOAD_LOG_DIR" \
  --max-workers 4 \
  --max-size-mb 0 \
  --discover \
  --resolve-workers 16 \
  --open-access-only \
  --no-auth \
  --dataset-retries 3 \
  --wget-tries 20 \
  --wget-timeout 120 \
  --wget-waitretry 30 \
  --sort size \
  --write-eeg-list "$DOWNLOAD_LOG_DIR/physionet_eeg_discovered.txt")
JOB=${JOB_RAW%%;*}

echo "Submitted PhysioNet all-EEG job: $JOB"
```

Siena-only download:

```bash
BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
DATA_ROOT=/mnt/ddn/shared/datasets/eeg
REPO=$BASE/repo
PY=$BASE/venv/bin/python
DATA_DIR=$REPO/data

cd "$DATA_DIR"
sbatch \
  --chdir="$DATA_DIR" \
  --output="$BASE/logs/slurm/siena-%j.out" \
  --error="$BASE/logs/slurm/siena-%j.err" \
  sbatch_download.sh \
  --data-source physionet \
  --python-bin "$PY" \
  --download-script-dir "$DATA_DIR" \
  --output-dir "$BASE/PhysioNet" \
  --log-dir "$BASE/logs/download" \
  --max-workers 1 \
  --max-size-mb 0 \
  --open-access-only \
  --no-auth \
  --datasets-file "$DATA_DIR/download_lists/physionet_siena.txt" \
  --sort size
```

Challenge 2018-only download uses
`data/download_lists/physionet_challenge2018.txt` in the same command shape.

## 5. Statistical Validation As `share`

Run after each dataset exists locally.

```bash
BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
cd "$BASE/repo"

bash data/statistical_analysis/run_openneuro_analysis.sh
bash data/statistical_analysis/run_siena_analysis.sh
bash data/statistical_analysis/run_physionet_challenge2018_analysis.sh
```

Smoke-test a wrapper on a limited number of files:

```bash
BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
cd "$BASE/repo"

MAX_FILES=100 bash data/statistical_analysis/run_siena_analysis.sh
MAX_FILES=100 bash data/statistical_analysis/run_openneuro_analysis.sh
```

Important outputs under `/mnt/ddn/shared/datasets/eeg/statistical_reports/*/`:

```text
dataset_inventory_summary.json
raw_eeg_index.csv
raw_eeg_errors.csv
pair_index.csv
pair_summary.json
statistics_summary.json
stats_tables/dataset_presentation_summary.csv
stats_tables/record_level_quality_flags.csv
stats_tables/header_warning_distribution.csv
stats_tables/channel_type_distribution.csv
stats_tables/physical_unit_distribution.csv
stats_tables/window_feasibility.csv
plot_data/
plots/
```
