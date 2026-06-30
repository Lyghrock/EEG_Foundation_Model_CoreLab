# EEG Data Download Manual

This repository currently handles OpenNeuro and PhysioNet download entrypoints.
TUH is intentionally kept outside this Slurm launcher flow and should be pulled
with the official TUH rsync command on the machine that has the external disk.

For copy-pasteable H100 commands split by `weijun` and `share`, see
`data/eeg_data_prepare_commands.md`.

## Environment

Create the runtime once on the machine that launches downloads:

```bash
cd /home/weijun/Brain_FM/EEG_Foundation_Model_CoreLab
conda create -y -n eeg_fm python=3.10
conda activate eeg_fm
python -m pip install -r requirements.txt
```

On CoRe_Lab_Server this environment has already been created at:

```text
/home/weijun/miniconda3/envs/eeg_fm
```

## Local PhysioNet Credential File

`data/config_physionet.json` is intentionally ignored by git. Keep it local to
the execution machine and restrict permissions:

```json
{
  "username": "Lyghrock",
  "password": "<local password>"
}
```

```bash
chmod 600 data/config_physionet.json
```

The PhysioNet downloader reads credentials in this priority order:

1. `--username` and `PHYSIONET_PASSWORD`
2. `PHYSIONET_USERNAME` and `PHYSIONET_PASSWORD`
3. `data/config_physionet.json`
4. `--ask-password`

Do not commit credential files or paste passwords into Slurm command lines.

## Shared Slurm Bundle

When the Slurm submission account cannot read `/home/weijun`, keep the runnable
download bundle under the shared EEG dataset root. Do this deployment from the
account that maintains the repository, then run Slurm from the download account:

```bash
mkdir -p /mnt/ddn/shared/datasets/eeg/download_scripts

cp data/sbatch_download.sh \
  data/download_OpenNeuro.py \
  data/download_PhysioNet.py \
  requirements.txt \
  /mnt/ddn/shared/datasets/eeg/download_scripts/
```

The launcher is relocatable. By default, it resolves `download_OpenNeuro.py` and
`download_PhysioNet.py` from the same directory as `sbatch_download.sh`, and logs
to `/mnt/ddn/shared/datasets/eeg/eeg_fm/logs/download`.

Under Slurm, the batch script may be copied into the scheduler spool directory
before execution. The launcher therefore first checks the job working directory
and then `SLURM_SUBMIT_DIR` for `download_*.py`. If submitting from another
directory, set `--download-script-dir /absolute/path/to/repo/data`.

For PhysioNet, also place the ignored credential file next to the copied
`download_PhysioNet.py`:

```bash
cp data/config_physionet.json \
  /mnt/ddn/shared/datasets/eeg/download_scripts/config_physionet.json
chmod 600 /mnt/ddn/shared/datasets/eeg/download_scripts/config_physionet.json
```

For cross-account Slurm jobs, prefer a plain Python/venv environment in the
shared tree. The launcher does not activate conda by default.

```bash
/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python -m pip install \
  -r /mnt/ddn/shared/datasets/eeg/eeg_fm/repo/requirements.txt
```

Submit OpenNeuro from the shared bundle:

```bash
cd /mnt/ddn/shared/datasets/eeg/download_scripts

sbatch sbatch_download.sh \
  --data-source openneuro \
  --download-backend aws \
  --max-workers 4 \
  --max-size-mb 0 \
  --sort size
```

Submit PhysioNet from the shared bundle:

```bash
cd /mnt/ddn/shared/datasets/eeg/download_scripts

sbatch sbatch_download.sh \
  --data-source physionet \
  --max-workers 4 \
  --max-size-mb 0 \
  --discover \
  --sort size
```

If `/mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python` exists, the launcher
uses it automatically. You can still override this explicitly:

```bash
sbatch sbatch_download.sh \
  --python-bin /mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python
```

## OpenNeuro

Dry-run all OpenNeuro EEG datasets:

```bash
conda run -n eeg_fm python data/download_OpenNeuro.py \
  --dry-run \
  --sort size
```

Download one small dataset for a smoke test:

```bash
conda run -n eeg_fm python data/download_OpenNeuro.py \
  --dataset ds003805 \
  --max-size-mb 500 \
  --max-workers 1 \
  --output-dir /tmp/openneuro_smoke_test
```

Default H100 storage through the Slurm launcher:

```text
/mnt/ddn/shared/datasets/eeg/eeg_fm/OpenNeuro
```

Slurm dry-run:

```bash
sbatch data/sbatch_download.sh \
  --data-source openneuro \
  --dry-run
```

OpenNeuro full download through Slurm:

```bash
BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
DATA_DIR=$BASE/repo/data
PY=$BASE/venv/bin/python
AWS_CFG=$BASE/aws/openneuro_aws_config

cd "$DATA_DIR"
sbatch \
  --export=ALL,AWS_CONFIG_FILE="$AWS_CFG" \
  --chdir="$DATA_DIR" \
  --output="$BASE/logs/slurm/openneuro-%j.out" \
  --error="$BASE/logs/slurm/openneuro-%j.err" \
  sbatch_download.sh \
  --data-source openneuro \
  --python-bin "$PY" \
  --download-script-dir "$DATA_DIR" \
  --output-dir "$BASE/OpenNeuro" \
  --log-dir "$BASE/logs/download" \
  --download-backend aws \
  --max-workers 2 \
  --max-size-mb 0 \
  --sort size \
  --heartbeat-sec 60 \
  --stall-timeout-min 180
```

If OpenNeuro downloads are slow or unstable, keep the public S3 backend and
reduce dataset-level workers. To inspect awscli itself, add one of these
diagnostic flags to the command above:

```bash
--aws-show-progress
--aws-debug
```

`--download-backend aws` uses OpenNeuro's public S3 bucket through `awscli`.
AWS CLI also has internal multipart/concurrent transfers, so use
`--max-workers 2` as the normal setting for very large datasets, then increase
only if the filesystem and network stay healthy. `--sort size` downloads larger
datasets first. `--heartbeat-sec` prints actual local byte growth, not only
completed AWS files. `--stall-timeout-min` terminates a single stuck backend
process after no local byte growth; rerunning resumes the same dataset.
Interrupted dataset directories are resumed on the next run; only directories
with `.download_complete.json` are skipped.

## PhysioNet

The PhysioNet downloader is strict by design: a dataset must show `EEG` or
`electroencephal*` evidence on the official project page or sampled file index
before it is allowed into the download queue. If evidence is absent, the dataset
is rejected even when it is open-access.

The downloader also rejects projects where EEG appears only as labels, adjunct
scoring, or derived spectra while the primary data are not raw EEG/PSG signals
for EEG-FM pretraining. Examples include calcium-imaging sleep-state projects,
heart-rate/accelerometry datasets with EEG sleep-stage labels, and multitaper
spectra datasets.

Validate one dataset URL:

```bash
conda run -n eeg_fm python data/download_PhysioNet.py \
  --dataset https://physionet.org/files/neuro-stress-resilience-hci/1.0.0/ \
  --dry-run
```

Known non-EEG rejection smoke test:

```bash
conda run -n eeg_fm python data/download_PhysioNet.py \
  --dataset https://physionet.org/files/butqdb/1.0.0/ \
  --dry-run
```

Discover EEG-related PhysioNet datasets from the official database list, the
PhysioNet challenge list, EEG/sleep/seizure topic pages, curated seed
candidates, and curated known EEG entries:

```bash
conda run -n eeg_fm python data/download_PhysioNet.py \
  --discover \
  --dry-run \
  --sort size \
  --write-eeg-list /tmp/physionet_eeg_discovered.txt
```

Show rejected datasets as well:

```bash
conda run -n eeg_fm python data/download_PhysioNet.py \
  --discover \
  --dry-run \
  --show-rejected
```

Check whether `challenge-2018/1.0.0` is included by the total PhysioNet
discovery flow:

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
```

For comparison, this checks the old database-only behavior. It may miss
`challenge-2018/1.0.0`, because that dataset is listed under PhysioNet
challenges rather than the normal database page:

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

grep -n '^challenge-2018/1.0.0' "$OUT" || \
  echo 'challenge-2018/1.0.0 not found by database-only discovery'
```

This checks the official challenge-list path only, without the curated known
EEG fallback:

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

Download all discovered EEG-validated PhysioNet datasets:

```bash
conda run -n eeg_fm python data/download_PhysioNet.py \
  --discover \
  --sort size \
  --max-workers 4 \
  --resolve-workers 6 \
  --output-dir /mnt/ddn/shared/datasets/eeg/PhysioNet
```

Download from a saved list:

```bash
conda run -n eeg_fm python data/download_PhysioNet.py \
  --datasets-file /tmp/physionet_eeg_discovered.txt \
  --max-workers 4 \
  --output-dir /mnt/ddn/shared/datasets/eeg/PhysioNet
```

## PhysioNet Challenge 2018

DeeperBrain's "PhysioNet 2018" entry refers to the specific
PhysioNet/Computing in Cardiology Challenge 2018 sleep arousal dataset:

```text
challenge-2018/1.0.0
```

This repo pins that dataset in:

```text
data/download_lists/physionet_challenge2018.txt
```

Because this dataset has been manually verified as EEG-containing PSG data, the
PhysioNet downloader includes a curated allowlist fallback for
`challenge-2018/1.0.0` when the metadata page is temporarily unreachable. This
does not relax automatic discovery for other PhysioNet datasets.

It should be downloaded through the same `sbatch_download.sh` PhysioNet flow as
other PhysioNet datasets. The expected H100 output location is:

```text
/mnt/ddn/shared/datasets/eeg/PhysioNet/challenge-2018/1.0.0
```

### H100 Cross-Account Workflow

Run this block as `weijun`. It updates the repo under the shared tree, creates a
copy-based venv that does not point into `/home/weijun`, prepares writable data
directories, and grants the `share` account access. This is the only part that
should touch git.

```bash
set -euo pipefail

BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
DATA_ROOT=/mnt/ddn/shared/datasets/eeg
REPO=$BASE/repo
PY=$BASE/venv/bin/python

mkdir -p "$BASE" \
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
chmod -R a+rwX "$BASE/PhysioNet" "$BASE/logs" "$BASE/statistical_reports" || \
  echo "[WARN] chmod skipped for some existing files not owned by weijun; share-side write checks below are authoritative"

if command -v setfacl >/dev/null 2>&1; then
  setfacl -R -m u:share:rX "$REPO" "$BASE/venv" || \
    echo "[WARN] setfacl read access had partial failures"
  setfacl -R -m u:share:rwX "$BASE/PhysioNet" "$BASE/logs" "$BASE/statistical_reports" || \
    echo "[WARN] setfacl write access had partial failures"
  setfacl -R -d -m u:share:rwX "$BASE/PhysioNet" "$BASE/logs" "$BASE/statistical_reports" || \
    echo "[WARN] setfacl default write access had partial failures"
fi

test -r "$REPO/data/sbatch_download.sh"
test -r "$REPO/data/download_PhysioNet.py"
test -r "$REPO/data/download_lists/physionet_challenge2018.txt"
test -x "$PY"
test ! -L "$BASE/venv/bin/python3"
bash -n "$REPO/data/sbatch_download.sh"
bash -n "$REPO/data/statistical_analysis/run_physionet_challenge2018_analysis.sh"
"$PY" -c "import requests, numpy, pandas, matplotlib, mne; print('weijun checks OK')"
```

Run this block as `share`. It never pulls git and never touches `/home/weijun`.
It verifies paths, permissions, Python imports, PhysioNet dry-run resolution,
and Slurm argument parsing before submitting the real download.

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
LIST=$DATA_DIR/download_lists/physionet_challenge2018.txt

cd "$DATA_DIR"

test "$(pwd -P)" = "$(cd "$DATA_DIR" && pwd -P)"
test -r sbatch_download.sh
test -r download_PhysioNet.py
test -r "$LIST"
test -x "$PY"
bash -n sbatch_download.sh
bash -n "$REPO/data/statistical_analysis/run_physionet_challenge2018_analysis.sh"
"$PY" -c "import requests, numpy, pandas, matplotlib, mne; print('share imports OK')"

touch "$PHYSIONET_ROOT/.share_write_test" && rm -f "$PHYSIONET_ROOT/.share_write_test"
touch "$DOWNLOAD_LOG_DIR/.share_write_test" && rm -f "$DOWNLOAD_LOG_DIR/.share_write_test"
touch "$SLURM_LOG_DIR/.share_write_test" && rm -f "$SLURM_LOG_DIR/.share_write_test"
touch "$BASE/statistical_reports/.share_write_test" && rm -f "$BASE/statistical_reports/.share_write_test"

"$PY" download_PhysioNet.py \
  --datasets-file "$LIST" \
  --output-dir "$PHYSIONET_ROOT" \
  --max-workers 1 \
  --max-size-mb 0 \
  --resolve-workers 1 \
  --sort size \
  --dry-run

if sbatch --help 2>&1 | grep -q -- "--test-only"; then
  sbatch --test-only \
    --chdir="$DATA_DIR" \
    --output="$SLURM_LOG_DIR/physionet2018-%j.out" \
    --error="$SLURM_LOG_DIR/physionet2018-%j.err" \
    sbatch_download.sh \
    --data-source physionet \
    --python-bin "$PY" \
    --download-script-dir "$DATA_DIR" \
    --output-dir "$PHYSIONET_ROOT" \
    --log-dir "$DOWNLOAD_LOG_DIR" \
    --max-workers 1 \
    --max-size-mb 0 \
    --dry-run \
    --datasets-file "$LIST" \
    --sort size
fi

DRY_JOB_RAW=$(sbatch --parsable \
  --chdir="$DATA_DIR" \
  --output="$SLURM_LOG_DIR/physionet2018-dryrun-%j.out" \
  --error="$SLURM_LOG_DIR/physionet2018-dryrun-%j.err" \
  sbatch_download.sh \
  --data-source physionet \
  --python-bin "$PY" \
  --download-script-dir "$DATA_DIR" \
  --output-dir "$PHYSIONET_ROOT" \
  --log-dir "$DOWNLOAD_LOG_DIR" \
  --max-workers 1 \
  --max-size-mb 0 \
  --dry-run \
  --datasets-file "$LIST" \
  --sort size)
DRY_JOB=${DRY_JOB_RAW%%;*}

echo "Submitted dry-run job: $DRY_JOB"
echo "Check it with:"
echo "  sacct -j $DRY_JOB --format=JobID,JobName,State,ExitCode,Elapsed,NodeList,Reason%80"
echo "  tail -n 200 $SLURM_LOG_DIR/physionet2018-dryrun-$DRY_JOB.out"
echo "  tail -n 200 $SLURM_LOG_DIR/physionet2018-dryrun-$DRY_JOB.err"
```

After the dry-run Slurm job succeeds, run this as `share` for the real
download:

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
LIST=$DATA_DIR/download_lists/physionet_challenge2018.txt

cd "$DATA_DIR"

JOB_RAW=$(sbatch --parsable \
  --chdir="$DATA_DIR" \
  --output="$SLURM_LOG_DIR/physionet2018-%j.out" \
  --error="$SLURM_LOG_DIR/physionet2018-%j.err" \
  sbatch_download.sh \
  --data-source physionet \
  --python-bin "$PY" \
  --download-script-dir "$DATA_DIR" \
  --output-dir "$PHYSIONET_ROOT" \
  --log-dir "$DOWNLOAD_LOG_DIR" \
  --max-workers 1 \
  --max-size-mb 0 \
  --datasets-file "$LIST" \
  --sort size)
JOB=${JOB_RAW%%;*}

echo "Submitted download job: $JOB"
echo "Monitor with:"
echo "  squeue -j $JOB"
echo "  tail -f $SLURM_LOG_DIR/physionet2018-$JOB.out"
echo "  tail -f $DOWNLOAD_LOG_DIR/physionet_${JOB}.log"
```

The downloader will create:

```text
/mnt/ddn/shared/datasets/eeg/PhysioNet/challenge-2018/1.0.0/.download_complete.json
```

after a successful run. Later runs skip the dataset when this marker exists.

After download, run the dataset-specific statistical-analysis wrapper as
`share` from the cloned repo:

```bash
BASE=/mnt/ddn/shared/datasets/eeg/eeg_fm
cd $BASE/repo
bash data/statistical_analysis/run_physionet_challenge2018_analysis.sh
```

The wrapper expands to the generic analysis command:

```bash
bash data/statistical_analysis/run_statistical_analysis.sh \
  --python-bin /mnt/ddn/shared/datasets/eeg/eeg_fm/venv/bin/python \
  --dataset-name PhysioNet_Challenge2018 \
  --input-root /mnt/ddn/shared/datasets/eeg/PhysioNet/challenge-2018/1.0.0 \
  --output-root /mnt/ddn/shared/datasets/eeg/statistical_reports \
  --workers 8 \
  --raw-formats hea
```

Use `--raw-formats hea` for this dataset. The `.mat` files are WFDB signal
payload components paired with `.hea` headers, not independent raw-record
entries.

Default H100 storage through the Slurm launcher:

```text
/mnt/ddn/shared/datasets/eeg/PhysioNet
```

Slurm PhysioNet discovery dry-run:

```bash
sbatch data/sbatch_download.sh \
  --data-source physionet \
  --dry-run \
  --discover \
  --sort size
```

Slurm PhysioNet discovered batch download:

```bash
sbatch data/sbatch_download.sh \
  --data-source physionet \
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
  --sort size
```

## Launcher Notes

`data/sbatch_download.sh` is source-generic and should be configured through
launcher arguments. Environment variables are still supported for compatibility,
but command-line arguments are less error-prone and take precedence:

```bash
sbatch data/sbatch_download.sh \
  --data-source openneuro \
  --download-script-dir /absolute/path/to/download_scripts \
  --output-dir /absolute/path/to/data/root \
  --log-dir /absolute/path/to/log/root \
  --python-bin /absolute/path/to/python \
  --max-workers 2 \
  --max-size-mb 0
```

The launcher does not pull git, sync files, or start preprocessing by default.
Preprocessing is intentionally off unless explicitly enabled:

```bash
ENABLE_PREPROCESS=true sbatch data/sbatch_download.sh
```

## Concurrency and Safety

- PhysioNet download workers write one dataset per final directory:
  `<output>/<slug>/<version>/`.
- Each PhysioNet dataset directory uses `.download.lock` to avoid two workers
  writing the same dataset at once.
- If a PhysioNet job is cancelled, stale `.download.lock` files can block the
  restart. Use `--lock-stale-min N` on the next run after cancelling the old job;
  locked datasets now make the job exit nonzero instead of pretending success.
- Full wget output is written to per-dataset logs under
  `<output-parent>/logs/physionet_datasets/`; the Slurm log keeps only
  attempt-level summaries unless `--verbose-wget` is passed.
- PhysioNet wget recursion uses `--level=inf`; the default wget recursion
  depth is too shallow for datasets with deeply nested EDF files.
- Dataset downloads are retried with `--dataset-retries`; checksum failure
  reruns wget to fill missing files before marking the dataset failed.
- A successful PhysioNet download writes `.download_complete.json`; later runs
  skip that dataset.
- `SHA256SUMS.txt` is checked automatically when present.
- Open-access-only mode is the default. Credentialed, restricted, or unknown
  access datasets are skipped unless `--no-open-access-only` is passed.
