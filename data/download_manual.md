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
to `/mnt/ddn/shared/datasets/eeg/logs/download`.

Under Slurm, the batch script may be copied into the scheduler spool directory
before execution. The launcher therefore prefers `SLURM_SUBMIT_DIR` when that
directory contains `download_*.py`. If submitting from another directory, set
`DOWNLOAD_SCRIPT_DIR=/absolute/path/to/repo/data`.

For PhysioNet, also place the ignored credential file next to the copied
`download_PhysioNet.py`:

```bash
cp data/config_physionet.json \
  /mnt/ddn/shared/datasets/eeg/download_scripts/config_physionet.json
chmod 600 /mnt/ddn/shared/datasets/eeg/download_scripts/config_physionet.json
```

For cross-account Slurm jobs, prefer a conda prefix in the shared tree. This
avoids relying on interactive shell conda activation:

```bash
conda create -y -p /mnt/ddn/shared/datasets/eeg/envs/eeg_fm python=3.10
/mnt/ddn/shared/datasets/eeg/envs/eeg_fm/bin/python -m pip install \
  -r /mnt/ddn/shared/datasets/eeg/download_scripts/requirements.txt
```

Submit OpenNeuro from the shared bundle:

```bash
cd /mnt/ddn/shared/datasets/eeg/download_scripts

DATA_SOURCE=openneuro \
MAX_WORKERS=8 \
MAX_SIZE_MB=0 \
sbatch sbatch_download.sh
```

Submit PhysioNet from the shared bundle:

```bash
cd /mnt/ddn/shared/datasets/eeg/download_scripts

DATA_SOURCE=physionet \
MAX_WORKERS=4 \
MAX_SIZE_MB=0 \
sbatch sbatch_download.sh --discover --sort size
```

If `/mnt/ddn/shared/datasets/eeg/envs/eeg_fm/bin/python` exists, the launcher
uses it automatically and skips `conda activate`. You can still override this
explicitly:

```bash
CONDA_ENV="" \
PYTHON_BIN=/mnt/ddn/shared/datasets/eeg/envs/eeg_fm/bin/python \
sbatch sbatch_download.sh
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
/mnt/ddn/shared/datasets/eeg/OpenNeuro
```

Slurm dry-run:

```bash
DATA_SOURCE=openneuro DRY_RUN=true sbatch data/sbatch_download.sh
```

OpenNeuro full download through Slurm:

```bash
DATA_SOURCE=openneuro \
MAX_WORKERS=8 \
MAX_SIZE_MB=0 \
sbatch data/sbatch_download.sh
```

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
/mnt/ddn/shared/datasets/eeg/PhysioNet
```

Slurm PhysioNet discovery dry-run:

```bash
DATA_SOURCE=physionet \
DRY_RUN=true \
sbatch data/sbatch_download.sh --discover --sort size
```

Slurm PhysioNet discovered batch download:

```bash
DATA_SOURCE=physionet \
MAX_WORKERS=4 \
MAX_SIZE_MB=0 \
sbatch data/sbatch_download.sh --discover --sort size
```

## Launcher Notes

`data/sbatch_download.sh` is source-generic and should be configured through
environment variables:

```bash
DATA_SOURCE=openneuro|physionet
DOWNLOAD_SCRIPT_DIR=/absolute/path/to/download_scripts
OUTPUT_DIR=/absolute/path/to/data/root
LOG_DIR=/absolute/path/to/log/root
CONDA_SH=/absolute/path/to/miniconda3/etc/profile.d/conda.sh
CONDA_ENV=eeg_fm
PYTHON_BIN=/absolute/path/to/python
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
