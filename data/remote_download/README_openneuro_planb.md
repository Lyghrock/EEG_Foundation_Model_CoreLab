# OpenNeuro Plan B Remote Download

This folder is for the overseas Windows + WSL machine with about 300 GB local
storage. It is independent from Slurm and from `data/sbatch_download.sh`.

## 1. Speed Test

Run the same command on H100 and on the overseas WSL machine:

```bash
python3 data/remote_download/download_OpenNeuro_planb.py speed-test \
  --dataset ds004024 \
  --sample-mb 1024 \
  --min-object-mb 2048
```

Interpretation:

- If both `awscli-range` and `curl-range` are slow on H100 but fast on WSL, the
  H100 network path is the bottleneck.
- If `curl-range` is fast but `awscli-range` is slow on the same host, tune or
  replace AWS CLI usage.
- If both are fast on H100, the existing Slurm job settings are likely the
  bottleneck.

## 2. Download With Automatic Cloud Upload

The script downloads object batches, uploads each batch, marks its objects as
uploaded in SQLite, then deletes local batch files.
Each object is downloaded with S3 byte ranges, so an interrupted large file
continues from its existing `.part` file in 512 MB chunks by default.

Example with `rclone`:

```bash
mkdir -p ~/openneuro_planb_stage

python3 data/remote_download/download_OpenNeuro_planb.py download \
  --output-dir ~/openneuro_planb_stage \
  --state-dir ./openneuro_planb_state \
  --log-dir ./openneuro_planb_logs \
  --local-budget-gb 250 \
  --batch-target-gb 50 \
  --object-chunk-mb 512 \
  --max-batches 0 \
  --sort size \
  --upload-command 'rclone copy "{batch_dir}" remote:OpenNeuro_PlanB --progress' \
  --delete-after-upload
```

For a first smoke test:

```bash
python3 data/remote_download/download_OpenNeuro_planb.py download \
  --output-dir ~/openneuro_planb_stage \
  --state-dir ./openneuro_planb_state \
  --batch-target-gb 5 \
  --max-batches 1 \
  --dataset ds000117 \
  --upload-command 'rclone copy "{batch_dir}" remote:OpenNeuro_PlanB_test --progress' \
  --delete-after-upload
```

## 3. Manual Upload Mode

If no upload command is configured, the script stops after one downloaded batch:

```bash
python3 data/remote_download/download_OpenNeuro_planb.py download \
  --output-dir ~/openneuro_planb_stage \
  --state-dir ./openneuro_planb_state \
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
