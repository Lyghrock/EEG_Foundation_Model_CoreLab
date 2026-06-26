# EEG Dataset Statistical Analysis Planning

## Goal

Build a reusable statistical-analysis toolkit for downloaded EEG datasets. The
toolkit must not be TUH-specific: it should work on any dataset directory whose
main raw signals are EEG recordings, including OpenNeuro, PhysioNet, TUH, and
later in-lab datasets.

The analysis has two layers:

1. Dataset-specific semantic report written by the agent before running the
   script: `report_{dataset_name}.md`.
2. Generic executable analysis launched by `run_statistical_analysis.sh`.

The generic scripts focus on file inventory, raw EEG metadata, pair/sidecar
structure, and statistical distributions needed before EEG-FM pretraining.

## Directory Layout

Target location:

```text
data/statistical_analysis/
  planning.md
  run_statistical_analysis.sh
  analyze_inventory.py
  analyze_raw_eeg.py
  compute_statistics.py
  plot_statistics.py
  subject_inference.py
```

The files below are planned modules; implementation should keep each module
single-purpose and callable from the shell launcher.

## Feature 0: Dataset Semantic Report

This feature is intentionally not implemented as a generic `.py`, because the
meaning of subfolders differs across datasets.

Workflow:

1. User tells the agent which dataset name and root directory to analyze.
2. Agent reads the official dataset page, README, data dictionary, and local
   folder tree.
3. Agent writes:

```text
data/statistical_analysis/reports/report_{dataset_name}.md
```

The report should include:

- Dataset name, source, version, license/access note.
- Local root path used for analysis.
- Raw EEG formats present.
- Expected subset structure, for example train/eval/test, task/session/run,
  corpus/version, subject/session folders, or BIDS hierarchy.
- What each major subfolder means.
- Approximate subject count and how subject IDs are encoded.
- Known annotation/clinical/metadata files and whether they are expected to
  pair with EEG recordings.
- Any dataset-specific caveats before running the generic analysis.

The agent then gives the user the exact command line to run
`run_statistical_analysis.sh`.

## Launcher

`run_statistical_analysis.sh` is the single user entry point.

Required arguments:

```bash
bash data/statistical_analysis/run_statistical_analysis.sh \
  --dataset-name TUH \
  --input-root /media/yizan/nevermind/tuh_eeg \
  --output-root /home/yizan/TUH_Download/statistical_reports
```

Optional arguments:

```text
--workers N                 parallel file-level metadata extraction
--raw-formats edf,bdf,vhdr,set,fif,gdf,cnt
--follow-symlinks true|false
--deep-signal-scan true|false
--max-files N               smoke-test limit
--overwrite true|false
```

Output directory:

```text
{output_root}/{dataset_name}_{timestamp}/
  dataset_inventory.csv
  dataset_inventory_summary.json
  raw_eeg_index.csv
  raw_eeg_errors.csv
  pair_index.csv
  pair_summary.json
  statistics_summary.json
  stats_tables/
    channel_count_distribution.csv
    sampling_frequency_distribution.csv
    duration_summary.csv
    sequence_length_summary.csv
  plot_data/
    plot_manifest.json
    channel_count_distribution.csv
    sampling_frequency_histogram.csv
    duration_histogram.csv
    sequence_length_histogram.csv
  plots/
    channel_count_distribution.png
    sampling_frequency_distribution.png
    duration_distribution.png
    sequence_length_distribution.png
  run_config.json
```

The launcher should use absolute paths internally and print all output paths at
startup.

## Module 1: `analyze_inventory.py`

Purpose: scan a given dataset root and report total data composition.

Inputs:

- `--dataset-name`
- `--input-root`
- `--output-dir`
- `--follow-symlinks`

Outputs:

- `dataset_inventory.csv`
- `dataset_inventory_summary.json`

Per-file CSV columns:

```text
path, relative_path, top_level_subset, parent_dir, suffix, size_bytes,
mtime_iso, is_symlink, symlink_target, inferred_subject_id,
inferred_session_id, inferred_task_id, file_role
```

`file_role` should be inferred by suffix/name:

- `raw_eeg`: `.edf`, `.bdf`, `.gdf`, `.vhdr`, `.eeg`, `.set`, `.fif`, `.cnt`
- `metadata`: `.json`, `.tsv`, `.csv`, `.xlsx`, `.mat`
- `annotation`: `.tse`, `.lbl`, `.rec`, `.vmrk`, event files, seizure labels
- `text_report`: `.txt`, `.md`, clinical reports
- `image_or_other`: remaining files

Summary JSON should include:

```json
{
  "dataset_name": "...",
  "input_root": "...",
  "total_files": 0,
  "total_size_bytes": 0,
  "raw_eeg_files": 0,
  "raw_eeg_size_bytes": 0,
  "metadata_files": 0,
  "annotation_files": 0,
  "file_count_by_suffix": {},
  "size_bytes_by_suffix": {},
  "file_count_by_top_level_subset": {},
  "raw_eeg_count_by_top_level_subset": {},
  "inferred_subject_count": 0,
  "inferred_session_count": 0
}
```

Subject/session/task inference is heuristic and should be transparent. It
should preserve raw path-derived IDs rather than over-normalizing them.

## Module 2: `analyze_raw_eeg.py`

Purpose: create one row per raw EEG recording, read EEG metadata, and estimate
how strongly raw EEG files are paired with metadata, annotations, events,
clinical reports, or labels.

Inputs:

- `dataset_inventory.csv`
- `--dataset-name`
- `--input-root`
- `--output-dir`
- `--workers`
- `--deep-signal-scan`

Outputs:

- `raw_eeg_index.csv`
- `raw_eeg_errors.csv`
- `pair_index.csv`
- `pair_summary.json`

Supported raw EEG formats in first implementation:

- EDF/EDF+: prefer `pyedflib` when available; fallback to lightweight manual
  EDF header parser.
- BDF/BDF+: use `pyedflib` or MNE fallback.
- BrainVision: `.vhdr` plus `.eeg`/`.vmrk`, use MNE metadata read.
- EEGLAB `.set`: use MNE metadata read when installed.
- FIF: use MNE metadata read.
- GDF/CNT: use MNE or optional library support when available.

Default behavior should avoid reading full signal payload. Deep signal scan is
optional and only used when the user explicitly requests it.

Raw EEG reader return schema:

```python
{
  "status": "OK|WARN|ERROR",
  "error": "",
  "format": "edf",
  "n_channels_total": 0,
  "n_eeg_channels": 0,
  "channel_names": [...],
  "channel_types": [...],
  "sampling_frequency_hz_min": 0.0,
  "sampling_frequency_hz_max": 0.0,
  "sampling_frequency_hz_mode": 0.0,
  "duration_sec": 0.0,
  "sequence_length_min": 0,
  "sequence_length_max": 0,
  "sequence_length_mode": 0,
  "physical_units": [...],
  "start_datetime": "",
  "header_warnings": []
}
```

Raw EEG index CSV columns:

```text
dataset_name, relative_path, absolute_path, file_format, size_bytes,
status, error, inferred_subject_id, inferred_session_id, inferred_task_id,
top_level_subset, n_channels_total, n_eeg_channels, channel_names,
channel_types, sampling_frequency_hz_min, sampling_frequency_hz_max,
sampling_frequency_hz_mode, duration_sec, sequence_length_min,
sequence_length_max, sequence_length_mode, physical_units, start_datetime,
header_warnings
```

Definition of sequence length:

- For raw EEG, sequence length means the number of time samples in the
  original recording before preprocessing.
- In the common case:

```text
sequence_length = duration_sec * sampling_frequency_hz
```

- For EDF/BDF files where channels may have different sample counts per data
  record, record min/max/mode sequence length across EEG channels.
- Patch length is a later modeling/preprocessing concept and is not this raw
  sequence length.

Pairing rules:

1. Exact same stem in the same directory:
   `record.edf` with `record.json`, `record.tsv`, `record.csv`,
   `record.tse`, `record.lbl`, `record.rec`, `record.vmrk`.
2. BIDS-style sidecars inherited from parent directories:
   `participants.tsv`, `sessions.tsv`, `*_scans.tsv`, `*_events.tsv`,
   `dataset_description.json`, task-level JSON sidecars.
3. TUH/NEDC-style annotation or report files in sibling label/report
   directories with matching relative stem.
4. Dataset-level metadata that applies globally.

Per-raw-file CSV columns:

```text
raw_relative_path, raw_suffix, inferred_subject_id, inferred_session_id,
paired_file_count, pair_types, exact_stem_pairs, inherited_metadata_pairs,
annotation_pairs, text_report_pairs, global_metadata_pairs, pair_score,
pair_status
```

`pair_status` values:

- `none`: no sidecar/metadata found.
- `global_only`: only dataset-level metadata found.
- `metadata_only`: subject/session/task metadata but no event/label/report.
- `annotation_or_label`: raw EEG has annotation/label/event pair.
- `rich_pair`: raw EEG has metadata plus annotation/report/clinical text.

This does not prove semantic correctness of labels; it estimates structural
pair availability.

## Module 3: `compute_statistics.py`

Purpose: compute JSON statistics from `raw_eeg_index.csv`,
`dataset_inventory_summary.json`, and `pair_summary.json`.

Outputs:

- `statistics_summary.json`
- table-ready CSV files under `stats_tables/`
- plot-ready CSV files under `plot_data/`
- `plot_data/plot_manifest.json`

Important boundary:

- `compute_statistics.py` owns all statistical computation and binning.
- `plot_statistics.py` must not recompute distributions. It should only render
  plot images from `plot_data/`.
- This separation lets the same outputs support plots, Markdown/LaTeX tables,
  spreadsheet inspection, and later dashboarding.

Variables to summarize:

- File size.
- Raw EEG count.
- Subject count.
- Pair status.
- Total channels and EEG channels.
- Sampling frequency in Hz.
- Duration in seconds/minutes/hours.
- Raw sequence length in samples.
- Format distribution.
- Top-level subset distribution.

Categorical/discrete summary:

Use categorical percentage tables when:

- unique value count <= 30, or
- value is semantically categorical, for example format, subset, pair status,
  channel count.

For channel count, always report percentage:

```json
{
  "n_eeg_channels_distribution": {
    "19": {"count": 1000, "percent": 35.2},
    "31": {"count": 800, "percent": 28.1}
  }
}
```

Table outputs should include both machine-friendly and human-friendly forms:

```text
stats_tables/channel_count_distribution.csv
stats_tables/sampling_frequency_value_counts.csv
stats_tables/duration_quantiles.csv
stats_tables/sequence_length_quantiles.csv
stats_tables/pair_status_distribution.csv
stats_tables/subject_recording_distribution.csv
stats_tables/subset_composition.csv
```

Numeric/continuous summary:

For sampling frequency, duration, and sequence length:

1. If unique values <= 30, include value-count percentages.
2. Always include numeric summary:

```json
{
  "count": 0,
  "missing": 0,
  "min": 0,
  "p01": 0,
  "p05": 0,
  "mean": 0,
  "median": 0,
  "p95": 0,
  "p99": 0,
  "max": 0,
  "std": 0,
  "iqr": 0
}
```

3. If unique values > 30, mark the variable as continuous for plotting.
4. For heavy-tailed duration/sequence-length values, also compute log10
   summaries for plotting.

Plot-data outputs should be fully rendered-data ready:

```text
plot_data/channel_count_distribution.csv
  value,count,percent

plot_data/sampling_frequency_histogram.csv
  bin_left,bin_right,bin_center,count,percent,density

plot_data/duration_histogram.csv
  bin_left_sec,bin_right_sec,bin_center_sec,count,percent,density

plot_data/sequence_length_histogram.csv
  bin_left_samples,bin_right_samples,bin_center_samples,count,percent,density

plot_data/{metric}_boxplot_summary.csv
  metric,min,q1,median,q3,max,iqr,whisker_low,whisker_high,outlier_count

plot_data/{metric}_ecdf.csv
  value,cumulative_count,cumulative_percent
```

`plot_manifest.json` should list every plot that can be generated, with source
CSV path, plot type, x/y columns, units, title, and recommended axis scale.

Quality flags:

- `missing_metadata_count`
- `zero_duration_count`
- `duration_outlier_count`
- `sampling_frequency_outlier_count`
- `channel_count_outlier_count`
- `sequence_length_outlier_count`
- `reader_error_count`

Outliers should be flagged by robust rules, not removed:

```text
low  = Q1 - 3 * IQR
high = Q3 + 3 * IQR
```

For EEG-FM readiness, the JSON should include a concise recommendation block:

```json
{
  "pretraining_readiness": {
    "dominant_sampling_frequencies_hz": [],
    "recommended_resample_targets_hz": [],
    "dominant_channel_counts": [],
    "duration_chunking_needed": true,
    "notes": []
  }
}
```

## Module 4: `plot_statistics.py`

Purpose: generate visual distributions from the prepared plot data.

Inputs:

- `plot_data/plot_manifest.json`
- CSV files under `plot_data/`

Outputs under `plots/`:

- `file_format_distribution.png`
- `raw_eeg_count_by_subset.png`
- `pair_status_distribution.png`
- `n_eeg_channels_distribution.png`
- `sampling_frequency_distribution.png`
- `duration_distribution.png`
- `duration_log10_distribution.png`
- `sequence_length_distribution.png`
- `sequence_length_log10_distribution.png`

Plot rules:

- Use bar charts for categorical or low-cardinality numeric values.
- Use the histogram bins generated by `compute_statistics.py`.
- Add KDE only if the corresponding density table is already generated by
  `compute_statistics.py`; do not estimate KDE inside plotting code.
- Always label axes with units:
  Hz, seconds, samples, file count, percentage.
- Plots are analysis artifacts, not publication figures.

No automatic generic report builder is planned. The dataset-specific
`report_{dataset_name}.md` remains an agent-authored semantic report, and the
script outputs structured CSV/JSON/PNG artifacts for inspection.

## Additional Statistics For EEG-FM Pretraining

Several EEG/Biosignal pretraining papers motivate statistics beyond basic
channel count, sampling frequency, duration, and sequence length:

- LaBraM (https://arxiv.org/abs/2405.18765) explicitly targets cross-dataset
  EEG learning under mismatched
  electrode counts, unequal sample lengths, varied task designs, and low
  signal-to-noise ratio; it reports pretraining on about 2,500 hours from
  around 20 EEG datasets.
- BENDR (https://arxiv.org/abs/2101.12037) emphasizes transfer across novel
  raw EEG sequences recorded with differing hardware, subjects, and tasks.
- EEGPT (https://openreview.net/forum?id=lvS2b8CjG5) frames universal EEG
  representation learning around low SNR, inter-subject variability, and
  channel mismatch.
- BIOT (https://arxiv.org/abs/2305.10351) handles mismatched channels,
  variable sample lengths, and missing values by tokenizing biosignals into a
  unified sequence-like representation, and its implementation examples
  standardize sampling rate/window/channel configurations.

Therefore the generic toolkit should also prepare the following statistics.

### Scale And Source Balance

Outputs:

```text
stats_tables/source_scale_summary.csv
stats_tables/recordings_by_dataset_subset.csv
stats_tables/hours_by_dataset_subset.csv
plot_data/source_hour_distribution.csv
```

Metrics:

- Total raw EEG hours.
- Raw recording count.
- Unique inferred subjects, sessions, tasks, and source subsets.
- Hours/recordings by dataset, corpus, subset, task, and format.
- Dominance ratios: percent of total hours contributed by the largest source,
  largest subject, largest task, and largest corpus.

Reason: a large corpus can be numerically dominated by one source, one task, or
one subject group, which matters for foundation-model pretraining.

### Subject And Session Balance

Outputs:

```text
stats_tables/subject_recording_distribution.csv
stats_tables/subject_hour_distribution.csv
stats_tables/session_distribution.csv
plot_data/subject_hours_histogram.csv
```

Metrics:

- Recordings per subject.
- Hours per subject.
- Sessions per subject.
- Recordings/hours per session.
- Missing or ambiguous subject IDs.
- Potential leakage groups for downstream split design.

Reason: EEGPT and related work emphasize inter-subject variability; a
pretraining index should reveal whether the dataset is balanced or heavily
skewed toward repeated recordings from a few subjects.

### Channel-Space Compatibility

Outputs:

```text
stats_tables/channel_name_frequency.csv
stats_tables/channel_set_patterns.csv
stats_tables/canonical_channel_coverage.csv
stats_tables/missing_channel_matrix.csv
plot_data/channel_coverage_heatmap.csv
```

Metrics:

- Channel-name frequency after raw and normalized channel naming.
- Distinct channel-set patterns and their percentages.
- Coverage of candidate canonical sets, for example 10-20 19-channel,
  TUH-style 21/23-channel, 32-channel, 64-channel.
- Per-record missing channels relative to each candidate canonical set.
- Reference/montage indicators if available in header or sidecars.
- Non-EEG auxiliary channels, for example EOG, ECG, EMG, RESP, annotation
  channels.

Reason: channel mismatch is one of the central problems in EEG foundation
models; these tables determine whether to use channel-specific tokens, channel
patches, interpolation, masking, or a restricted canonical channel set.

### Sampling-Rate And Resampling Readiness

Outputs:

```text
stats_tables/sampling_frequency_value_counts.csv
stats_tables/resample_target_candidates.csv
plot_data/sampling_frequency_distribution.csv
```

Metrics:

- Sampling-frequency distribution by record and by hours.
- Mixed-rate files where different channels have different sampling rates.
- Candidate resampling targets and retained-hour percentages, such as 100,
  128, 200, 250, 256, 500 Hz.
- Estimated downsampling/upsampling factors.
- Records below each candidate target that would require upsampling or
  exclusion.

Reason: BIOT-style workflows often standardize sampling frequency before
tokenization; EEG-FM preprocessing needs evidence for choosing a target rate.

### Windowing, Token Budget, And Patch Feasibility

Outputs:

```text
stats_tables/window_feasibility.csv
stats_tables/patch_token_budget.csv
plot_data/window_count_by_duration.csv
```

Metrics:

- Number of valid non-overlapping windows per record for candidate windows:
  1s, 2s, 4s, 5s, 10s, 30s, 60s.
- Same counts for common overlaps, for example 50%.
- Discarded tail seconds/samples under each window setting.
- Estimated channel-token counts:

```text
tokens = n_eeg_channels * ceil(duration_sec / patch_duration_sec)
```

- Percent of records exceeding candidate transformer token budgets.

Reason: LaBraM-style channel patches and BIOT-style fixed windows make
duration/sequence-length statistics operational; this tells us whether to
chunk, crop, pad, or cap token counts.

### Signal-Quality Optional Deep Scan

This should be optional because it requires reading signal payloads.

Outputs:

```text
stats_tables/deep_signal_quality_by_record.csv
stats_tables/deep_signal_quality_summary.csv
stats_tables/bandpower_summary.csv
plot_data/amplitude_distribution.csv
plot_data/flatline_ratio_distribution.csv
plot_data/clipping_ratio_distribution.csv
plot_data/line_noise_ratio_distribution.csv
```

Metrics:

- NaN/Inf count.
- Flatline ratio.
- Digital or physical clipping/saturation ratio.
- Per-channel mean, standard deviation, min, max, robust MAD.
- Extreme-amplitude outlier rates.
- Approximate line-noise indicators near 50/60 Hz when sampling rate supports
  it.
- Coarse PSD bandpower summaries: delta, theta, alpha, beta, gamma, if
  computationally acceptable.
- Non-EEG channel contamination and auxiliary-channel counts.

Reason: low SNR is repeatedly identified as a foundation-model challenge; this
optional pass helps decide cleaning, rejection, or quality weighting.

### Metadata, Labels, And Pairing Readiness

Outputs:

```text
stats_tables/pair_status_distribution.csv
stats_tables/metadata_field_coverage.csv
stats_tables/label_file_coverage.csv
```

Metrics:

- Structural pair status per raw recording.
- Sidecar/annotation/report availability by source subset.
- Common metadata fields and missingness, for example age, sex, diagnosis,
  task, session, recording date, sampling rate.
- Label/annotation time coverage where label files expose intervals.
- Whether label intervals exceed raw duration or have negative/overlapping
  times.

Reason: this does not validate label semantics, but it tells us whether a
dataset can support supervised downstream tasks or multimodal signal-text
experiments later.

### Duplication And Version Overlap

Outputs:

```text
stats_tables/duplicate_file_candidates.csv
stats_tables/version_overlap_summary.csv
```

Metrics:

- Duplicate paths/stems.
- Duplicate file sizes and optional checksums for small files.
- Same subject/session/task repeated across version folders.
- Old-version versus current-version composition.

Reason: TUH-like downloads can contain multiple versions; pretraining should
avoid silently overweighting duplicated recordings.

### Pretraining Readiness Summary

`statistics_summary.json` should contain a `pretraining_readiness` block with
actionable configuration suggestions:

```json
{
  "pretraining_readiness": {
    "dominant_sampling_frequencies_hz": [],
    "recommended_resample_targets_hz": [],
    "dominant_channel_counts": [],
    "candidate_canonical_channel_sets": [],
    "recommended_window_lengths_sec": [],
    "estimated_total_train_windows": {},
    "duration_chunking_needed": true,
    "major_quality_risks": [],
    "major_balance_risks": [],
    "major_pairing_risks": [],
    "notes": []
  }
}
```

## Implementation Order

1. Create `run_statistical_analysis.sh` with argument parsing and path checks.
2. Implement `analyze_inventory.py`.
3. Implement `subject_inference.py` helper and reuse it in inventory/raw index.
4. Implement `analyze_raw_eeg.py` with integrated EEG metadata readers and
   structural pair detection.
5. Implement `compute_statistics.py`, including table-ready and plot-ready
   CSV generation.
6. Implement `plot_statistics.py` as a render-only consumer of `plot_data/`.
7. Smoke-test on a small TUH/OpenNeuro subset, then full-run on one dataset.

## Dependency Plan

Minimum:

```text
numpy
pandas
matplotlib
tqdm
```

Recommended:

```text
pyedflib
mne
scipy
seaborn
openpyxl
```

The code should degrade gracefully:

- EDF metadata can still be parsed without MNE/pyedflib using manual header
  parsing.
- Non-EDF formats can be marked `ERROR` with clear dependency messages if MNE
  is unavailable.
- Plot KDE can be skipped if scipy/seaborn is unavailable.

## Notes For Current TUH Use

The current Sirius TUH validation script is useful as a prototype, but the
final repo implementation should not be copied as TUH-only code. The reusable
tool should scan EEG format metadata and dataset structure generically, while
dataset-specific interpretation remains in `report_{dataset_name}.md`.

For a future TUH run, the agent should first create:

```text
data/statistical_analysis/reports/report_TUH.md
```

Then provide a command similar to:

```bash
bash data/statistical_analysis/run_statistical_analysis.sh \
  --dataset-name TUH \
  --input-root /media/yizan/nevermind/tuh_eeg \
  --output-root /home/yizan/TUH_Download/statistical_reports \
  --workers 8 \
  --raw-formats edf \
  --deep-signal-scan false
```

Deep signal payload scanning should remain optional because it is much slower
than metadata/statistical indexing and is not required for channel count,
sampling frequency, duration, or raw sequence length.
