# OpenNeuro EEG Dataset Collection Report

## Identity

- Dataset name used by this repo: `OpenNeuro`
- Source: OpenNeuro EEG-modality datasets downloaded by `download_OpenNeuro.py`
- Default H100 root: `/mnt/ddn/shared/datasets/eeg/OpenNeuro`

## Modality And Relevance

OpenNeuro is a collection of BIDS-like public datasets rather than one corpus.
The local root contains many dataset folders, usually named with OpenNeuro
dataset IDs such as `ds00xxxx`. Individual datasets may use EDF/BDF,
BrainVision, EEGLAB, FIF, GDF, CNT, or other EEG-compatible containers.

## Expected Structure

Expected local layout:

```text
/mnt/ddn/shared/datasets/eeg/OpenNeuro/
  dsXXXXXX/
    dataset_description.json
    participants.tsv
    sub-*/
      ses-*/
      eeg/
      ...
```

Some datasets may have partial downloads, nonstandard folder depths, or
non-EEG auxiliary files. The generic inventory should be treated as the source
of truth for what is actually present locally.

## Validation Entry Point

Run:

```bash
bash data/statistical_analysis/run_openneuro_analysis.sh
```

The wrapper uses:

```text
--raw-formats edf,bdf,gdf,vhdr,set,fif,fif.gz,cnt,mff,hea
--input-root /mnt/ddn/shared/datasets/eeg/OpenNeuro
```

## Metrics To Inspect

The generic statistical analysis writes TUH-style corpus metrics plus
OpenNeuro-specific BIDS sidecar coverage:

- `dataset_inventory_summary.json`: file count, total size, raw EEG size, raw
  component size, suffix distribution, dataset-subfolder distribution.
- `raw_eeg_index.csv`: one row per raw EEG recording with format, header status,
  channels, sampling rate, duration, sequence length, units, and warnings.
- `raw_eeg_errors.csv`: files whose headers could not be read.
- `pair_index.csv`: BIDS sidecars and same-stem metadata/annotation pairs.
- `pair_summary.json`: pair-status percentages.
- `stats_tables/file_format_distribution.csv`: raw container diversity.
- `stats_tables/subset_composition.csv`: OpenNeuro dataset-level contribution
  by `ds*` top-level folder.
- `stats_tables/channel_set_patterns.csv`: repeated channel montages and
  dataset-specific channel layouts.
- `stats_tables/canonical_channel_coverage.csv`: compatibility with candidate
  canonical EEG channel sets.
- `stats_tables/record_level_quality_flags.csv`: per-record legality flags.
- `stats_tables/header_warning_distribution.csv`: aggregate header issues.
- `stats_tables/channel_type_distribution.csv` and
  `stats_tables/physical_unit_distribution.csv`: channel-type/unit sanity.
- `stats_tables/raw_file_size_bytes_summary.csv`,
  `stats_tables/bytes_per_hour_summary.csv`, and
  `stats_tables/bytes_per_sample_channel_summary.csv`: storage-vs-signal scale
  checks.
- `stats_tables/window_feasibility.csv`: fixed-window counts for pretraining.
- `stats_tables/dataset_presentation_summary.csv`: one-row paper-style summary.

## Caveats

- OpenNeuro is heterogeneous; one anomalous dataset can dominate error counts
  or unusual channel formats. Always inspect `subset_composition.csv`,
  `raw_eeg_errors.csv`, and `record_level_quality_flags.csv` together.
- Structural pairing does not validate BIDS semantic correctness; it reports
  sidecar presence and likely pairing only.
- If MNE lacks a backend for a specific format, those files will appear in
  `raw_eeg_errors.csv` with the import/reader error.
