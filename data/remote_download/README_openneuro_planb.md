# OpenNeuro Plan B Remote Download

This folder is for the overseas Windows + WSL machine with about 300 GB local
storage. It is independent from Slurm and from `data/sbatch_download.sh`.

Use `run_OpenNeuro_planb.sh` as the single entry point. The wrapper checks
Python and prevents two concurrent runs from using the same state directory.
By default it does not install `awscli`; the Python script uses standard-library
`urllib` and available `curl`, and can use `awscli` only if you explicitly make
it available.

The downloader lists OpenNeuro datasets through GraphQL and S3 REST metadata.
For actual file transfer it can use `awscli`, `urllib`, or `curl`; with
`--transfer-backend auto` it benchmarks the available backends and uses the
fastest one for the batch.

S3 listing is treated as a resumable process. Each successfully listed S3 page
is immediately written to the SQLite state DB, and the last listed key is saved
so a later run can resume the same dataset instead of starting over. Per-dataset
listing logs are written under:

```text
$HOME/openneuro_planb/logs/listing/dsXXXXXX.list.log
```

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

## 2. Default Manual-Upload Batch Download

The default mode downloads one bounded object batch and then stops. Upload the
printed `batch_dir` manually. After the upload is complete, run the same
`download` command again: it first marks the previous downloaded batch as
uploaded, removes that local batch directory, and then continues with the next
batch.

Each object is downloaded with S3 byte ranges, so an interrupted large file
continues from its existing `.part` file in 512 MB chunks by default.
Before stopping, each batch also writes `_planb_manifests/quality_report_*.json`
with file counts, total size, extension statistics, and sampled JSON/TSV/binary
read checks.

Default values are already set in `run_OpenNeuro_planb.sh`. The script no
longer runs any upload command by itself; every `download` invocation stages
exactly one manually uploaded batch.

```text
OUTPUT_DIR=./openneuro_planb_stage
STATE_DIR=$HOME/openneuro_planb/state
LOG_DIR=$HOME/openneuro_planb/logs
PLANB_LOCAL_BUDGET_GB=230
PLANB_BATCH_TARGET_GB=200
PLANB_MIN_FREE_GB=20
PLANB_MAX_WORKERS=8
PLANB_TRANSFER_BACKEND=auto
PLANB_BACKEND_PROBE_MB=256
PLANB_OBJECT_CHUNK_MB=512
PLANB_RETRIES=5
PLANB_AUTO_MARK_PREVIOUS_UPLOADED=true
```

Run one batch:

```bash
cd data/remote_download
./run_OpenNeuro_planb.sh download
```

After the command stops, upload the printed batch directory:

```text
./openneuro_planb_stage/batch_000001
```

After that upload has finished, run the same command again:

```bash
./run_OpenNeuro_planb.sh download
```

On this second run, `batch_000001` is marked uploaded and removed locally before
`batch_000002` starts. Repeat this cycle until the script prints `[DONE] no
pending objects remain`.

Run one batch for a specific dataset:

```bash
./run_OpenNeuro_planb.sh download --dataset ds004395
```

For a small smoke test:

```bash
bash data/remote_download/run_OpenNeuro_planb.sh download \
  --dataset ds000117 \
  --batch-target-gb 0.001 \
  --local-budget-gb 1 \
  --min-free-gb 0.1 \
  --max-workers 1
```

If you ever need to disable the automatic mark-and-clean step because the
previous batch has not actually been uploaded yet:

```bash
PLANB_AUTO_MARK_PREVIOUS_UPLOADED=false ./run_OpenNeuro_planb.sh download
```

## 4. Status

```bash
./run_OpenNeuro_planb.sh status
```

## 5. Merge Back To H100

Each uploaded batch contains top-level `dsXXXXXX/...` paths. Copy the cloud
folder contents into the final OpenNeuro root:

```bash
rclone copy remote:OpenNeuro_PlanB /mnt/ddn/shared/datasets/eeg/OpenNeuro --progress
```

The `_planb_manifests/` files uploaded with each batch can be kept as provenance
or excluded during final cleanup after verification.
