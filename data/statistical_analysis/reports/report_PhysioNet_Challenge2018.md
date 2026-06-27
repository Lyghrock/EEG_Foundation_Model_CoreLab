# PhysioNet Challenge 2018 Dataset Report

## Identity

- Dataset name used by this repo: `PhysioNet_Challenge2018`
- PhysioNet project id: `challenge-2018/1.0.0`
- Official title: You Snooze, You Win: the PhysioNet/Computing in Cardiology Challenge 2018
- Official project page: https://physionet.org/content/challenge-2018/1.0.0/
- Official file root: https://physionet.org/files/challenge-2018/1.0.0/
- Default H100 download root:
  `/mnt/ddn/shared/datasets/eeg/PhysioNet/challenge-2018/1.0.0`

This is a specific PhysioNet/CinC Challenge 2018 sleep arousal dataset, not a
general umbrella name for all EEG datasets on PhysioNet. Goldberger et al. is
the platform citation for PhysioNet; Ghassemi et al. is the challenge dataset
citation.

## Modality And Relevance

The dataset is polysomnography-style physiological time-series data. It is
EEG-relevant because the signal channels include EEG together with auxiliary
PSG channels such as EOG, EMG, ECG, oxygen saturation, and respiratory signals.
For EEG-FM pretraining, the analysis should separate EEG channel counts from
total PSG channel counts and keep auxiliary channels visible as metadata rather
than silently treating them as EEG.

## Expected Local Structure

After running `download_PhysioNet.py` through `sbatch_download.sh`, the expected
root is:

```text
/mnt/ddn/shared/datasets/eeg/PhysioNet/challenge-2018/1.0.0/
```

Typical first-level content from the official files page includes training and
test/evaluation-related directories and challenge support files. Exact names
should be verified from the local `dataset_inventory.csv` after download,
because PhysioNet challenge folders may contain documentation, scoring tools,
sample files, or checksum files in addition to raw records.

## Record File Pattern

The raw signals use WFDB-style records:

```text
record_id.hea
record_id.mat
record_id.arousal
record_id-arousal.mat
```

Interpretation:

- `.hea`: WFDB header. This is the primary raw record discovered by the generic
  statistical analysis with `--raw-formats hea`.
- same-stem `.mat`: signal payload component referenced by the `.hea` header.
- `.arousal` and `*-arousal.mat`: sleep/arousal annotation or label files.

The generic statistical analysis treats `.hea` as the raw record and reports
same-stem `.mat`, `.arousal`, and `*-arousal.mat` as structural pairs.

## Subject / Recording IDs

Challenge-style record folders and stems such as `tr01-0001` or `te01-0001`
are treated as record-level subject identifiers by the generic path inference
logic. This is a pragmatic grouping key for corpus statistics; it does not
assert that every folder is a clinically independent patient unless the
official metadata confirms that.

## Statistics To Inspect After Download

Run:

```bash
bash data/statistical_analysis/run_physionet_challenge2018_analysis.sh
```

The most important outputs are:

- `dataset_inventory_summary.json`: total file count, size, raw component size,
  top-level folders, and suffix distribution.
- `raw_eeg_index.csv`: one row per `.hea` WFDB record with channel labels,
  sampling frequency, duration, and sequence length.
- `pair_index.csv`: `.hea` to `.mat` / `.arousal` structural pairing.
- `pair_summary.json`: annotation coverage and pair-status percentages.
- `stats_tables/dataset_presentation_summary.csv`: one-row paper-style summary
  for corpus tables.
- `stats_tables/channel_name_frequency.csv`: EEG and auxiliary channel naming.
- `stats_tables/window_feasibility.csv`: fixed-window counts, especially useful
  for 30 s sleep windows.

## Caveats

- Structural pairing only checks files with matching stems; it does not verify
  label semantics or clinical correctness.
- `.mat` payload size is counted as raw data component size, while `.hea` size
  is only header size. For total raw storage scale, prefer `raw_data_size_gb`
  over `raw_eeg_header_size_gb` in `dataset_presentation_summary.csv`.
- Use `--raw-formats hea` for this dataset. Including generic `.mat` as a raw
  format would be incorrect here because the `.mat` files are WFDB payload
  components, not independent records.
