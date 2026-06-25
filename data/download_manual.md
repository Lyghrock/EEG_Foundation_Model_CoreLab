# EEG Data Download Manual

This repository currently handles OpenNeuro and PhysioNet download entrypoints.
TUH is intentionally kept outside this Slurm launcher flow and should be pulled
with the official TUH rsync command on the machine that has the external disk.

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
sbatch data/sbatch_download.sh \
  --data-source openneuro \
  --download-backend aws \
  --max-workers 4 \
  --max-size-mb 0 \
  --sort size
```

If OpenNeuro downloads are slow or unstable, keep the public S3 backend and
reduce dataset-level workers:

```bash
sbatch data/sbatch_download.sh \
  --data-source openneuro \
  --download-backend aws \
  --max-workers 2 \
  --max-size-mb 0 \
  --sort size
```

`--download-backend aws` uses OpenNeuro's public S3 bucket through `awscli`.
AWS CLI also has internal multipart/concurrent transfers, so use
`--max-workers 4` as the normal setting and reduce to `2` if the filesystem or
network becomes saturated. `--sort size` downloads larger datasets first.
Interrupted dataset directories are resumed on the next run; only directories
with `.download_complete.json` are skipped.

## PhysioNet

The PhysioNet downloader is strict by design: a dataset must show `EEG` or
`electroencephal*` evidence on the official project page or sampled file index
before it is allowed into the download queue. If evidence is absent, the dataset
is rejected even when it is open-access.

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

Discover EEG-related PhysioNet datasets from the official database list:

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

Default H100 storage through the Slurm launcher:

```text
/mnt/ddn/shared/datasets/eeg/eeg_fm/PhysioNet
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
- A successful PhysioNet download writes `.download_complete.json`; later runs
  skip that dataset.
- `SHA256SUMS.txt` is checked automatically when present.
- Avoid very high `MAX_WORKERS` for credentialed PhysioNet downloads; start with
  2-4 workers and increase only if the server behaves well.
