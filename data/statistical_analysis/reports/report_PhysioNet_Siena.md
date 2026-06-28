# PhysioNet Siena Scalp EEG Dataset Report

## Identity

- Dataset name used by this repo: `PhysioNet_Siena`
- PhysioNet project id: `siena-scalp-eeg/1.0.0`
- Official project page: https://physionet.org/content/siena-scalp-eeg/1.0.0/
- Default H100 root:
  `/mnt/ddn/shared/datasets/eeg/PhysioNet/siena-scalp-eeg/1.0.0`

## Modality And Relevance

Siena is an adult scalp EEG seizure dataset. It is directly relevant for EEG-FM
pretraining and later seizure-related downstream checks. The primary raw signal
files are EDF recordings; subject-level text files describe seizure timing and
recording notes.

## Expected Structure

Expected local layout after PhysioNet download:

```text
/mnt/ddn/shared/datasets/eeg/PhysioNet/siena-scalp-eeg/1.0.0/
  PN00/
    *.edf
    Seizures-list-*.txt
  PN01/
    *.edf
    Seizures-list-*.txt
  ...
```

Exact subject folder names and counts should be verified from
`dataset_inventory.csv`, because the downloader mirrors the official PhysioNet
file tree.

## Validation Entry Point

Run:

```bash
bash data/statistical_analysis/run_siena_analysis.sh
```

The wrapper uses:

```text
--raw-formats edf
--input-root /mnt/ddn/shared/datasets/eeg/PhysioNet/siena-scalp-eeg/1.0.0
```

## Metrics To Inspect

The generic statistical analysis now writes TUH-style corpus metrics plus
additional legality and signal-structure checks:

- `raw_eeg_index.csv`: EDF header status, channel names/types, sampling rate,
  duration, sequence length, physical units, start time, and header warnings.
- `raw_eeg_errors.csv`: unreadable or malformed EDF files.
- `pair_index.csv`: structural pairing to subject-level seizure/report text.
- `pair_summary.json`: pair status percentages.
- `stats_tables/record_level_quality_flags.csv`: per-record quality flags such
  as reader errors, zero duration, mixed sampling rate, zero EEG channels, and
  header warnings.
- `stats_tables/header_warning_distribution.csv`: aggregate EDF/WFDB warnings.
- `stats_tables/channel_type_distribution.csv`: EEG/auxiliary/annotation
  channel counts inferred from headers.
- `stats_tables/physical_unit_distribution.csv`: header physical-unit sanity.
- `stats_tables/raw_file_size_bytes_summary.csv`,
  `stats_tables/bytes_per_hour_summary.csv`, and
  `stats_tables/bytes_per_sample_channel_summary.csv`: storage-vs-signal scale
  checks.
- `stats_tables/window_feasibility.csv`: fixed-window sample counts.
- `stats_tables/dataset_presentation_summary.csv`: one-row paper-style corpus
  summary.

## Caveats

- Structural pairing does not verify seizure-label semantics; it only checks
  that likely report/annotation files exist near the EDF records.
- If a subject-level seizure list covers multiple EDF recordings, the generic
  pair index will mark it as a local text pair for all EDFs in that subject
  folder.
