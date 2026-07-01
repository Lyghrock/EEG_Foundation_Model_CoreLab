# OpenNeuro Plan B Remote Download

This folder is for the overseas Windows + WSL machine with about 300 GB local
storage. It is independent from Slurm and from `data/sbatch_download.sh`.

Use `run_OpenNeuro_planb.sh` as the single entry point. The wrapper checks
Python, prevents two concurrent runs from using the same state directory, and
tries to install `awscli` into a local runtime venv. If that optional install
fails, the Python script still falls back to standard-library `urllib` and
available `curl`.

The downloader lists OpenNeuro datasets through GraphQL and S3 REST metadata.
For actual file transfer it can use `awscli`, `urllib`, or `curl`; with
`--transfer-backend auto` it benchmarks the available backends and uses the
fastest one for the batch.

## 1. Speed Test

Run the same command on H100 and on the overseas WSL machine:

```bash
bash data/remote_download/run_OpenNeuro_planb.sh speed-test \
  --dataset ds004024 \
  --sample-mb 256 \
  --min-object-mb 512
```

Interpretation:

- If all backends are slow on H100 but fast on WSL, the H100 network path is the
  bottleneck.
- If one backend is much faster on the same host, use that backend explicitly or
  keep `--transfer-backend auto`.
- If all backends are fast on H100, the existing Slurm job settings are likely
  the bottleneck.

## 2. Download With Automatic Cloud Upload

The script downloads object batches, uploads each batch, marks its objects as
uploaded in SQLite, then deletes local batch files.
Each object is downloaded with S3 byte ranges, so an interrupted large file
continues from its existing `.part` file in 512 MB chunks by default.
Before upload, each batch also writes `_planb_manifests/quality_report_*.json`
with file counts, total size, extension statistics, and sampled JSON/TSV/binary
read checks.

Example with `rclone`:

```bash
mkdir -p ~/openneuro_planb_stage

OUTPUT_DIR=~/openneuro_planb_stage \
STATE_DIR=./openneuro_planb_state \
LOG_DIR=./openneuro_planb_logs \
UPLOAD_COMMAND='rclone copy "{batch_dir}" remote:OpenNeuro_PlanB --progress' \
bash data/remote_download/run_OpenNeuro_planb.sh download \
  --local-budget-gb 250 \
  --batch-target-gb 220 \
  --min-free-gb 20 \
  --max-workers 4 \
  --transfer-backend auto \
  --backend-probe-mb 256 \
  --object-chunk-mb 512 \
  --max-batches 0 \
  --sort size \
  --delete-after-upload
```

For a first smoke test:

```bash
OUTPUT_DIR=~/openneuro_planb_stage \
STATE_DIR=./openneuro_planb_state \
bash data/remote_download/run_OpenNeuro_planb.sh download \
  --batch-target-gb 5 \
  --max-batches 1 \
  --dataset ds000117 \
  --upload-command 'rclone copy "{batch_dir}" remote:OpenNeuro_PlanB_test --progress' \
  --delete-after-upload
```

## 3. Manual Upload Mode

Continuous mode requires an upload command. To intentionally stage one local
batch without uploading, disable continuous mode and cap the run to one batch:

```bash
OUTPUT_DIR=~/openneuro_planb_stage \
STATE_DIR=./openneuro_planb_state \
PLANB_CONTINUOUS=false \
bash data/remote_download/run_OpenNeuro_planb.sh download \
  --batch-target-gb 50 \
  --max-batches 1
```

Upload the printed `batch_dir` manually. Then mark it uploaded:

```bash
python3 data/remote_download/download_OpenNeuro_planb.py mark-uploaded \
  --state-dir ./openneuro_planb_state \
  --batch-id batch_YYYYMMDD_HHMMSS \
  --delete-after-upload
```

## 4. Status

```bash
python3 data/remote_download/download_OpenNeuro_planb.py status \
  --state-dir ./openneuro_planb_state
```

## 5. Merge Back To H100

Each uploaded batch contains top-level `dsXXXXXX/...` paths. Copy the cloud
folder contents into the final OpenNeuro root:

```bash
rclone copy remote:OpenNeuro_PlanB /mnt/ddn/shared/datasets/eeg/OpenNeuro --progress
```

The `_planb_manifests/` files uploaded with each batch can be kept as provenance
or excluded during final cleanup after verification.
