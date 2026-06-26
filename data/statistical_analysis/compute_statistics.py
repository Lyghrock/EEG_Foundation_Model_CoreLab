#!/usr/bin/env python3
"""Compute table-ready and plot-ready statistics for EEG dataset analysis."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np


CANONICAL_CHANNEL_SETS = {
    "10_20_19": {
        "FP1",
        "FP2",
        "F3",
        "F4",
        "C3",
        "C4",
        "P3",
        "P4",
        "O1",
        "O2",
        "F7",
        "F8",
        "T7",
        "T8",
        "P7",
        "P8",
        "FZ",
        "CZ",
        "PZ",
    },
    "tuh_common_21": {
        "FP1",
        "FP2",
        "F3",
        "F4",
        "C3",
        "C4",
        "P3",
        "P4",
        "O1",
        "O2",
        "F7",
        "F8",
        "T3",
        "T4",
        "T5",
        "T6",
        "A1",
        "A2",
        "FZ",
        "CZ",
        "PZ",
    },
    "10_20_21_modern": {
        "FP1",
        "FP2",
        "F3",
        "F4",
        "C3",
        "C4",
        "P3",
        "P4",
        "O1",
        "O2",
        "F7",
        "F8",
        "T7",
        "T8",
        "P7",
        "P8",
        "A1",
        "A2",
        "FZ",
        "CZ",
        "PZ",
    },
}
RESAMPLE_TARGETS = [100, 128, 200, 250, 256, 500]
WINDOW_SECONDS = [1, 2, 4, 5, 10, 30, 60]
PATCH_SECONDS = [0.25, 0.5, 1, 2, 4]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    return out if math.isfinite(out) else None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    if f is None:
        return None
    return int(round(f))


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    return float(np.quantile(np.asarray(values, dtype=float), q))


def numeric_summary(values: list[float | None]) -> dict[str, Any]:
    valid = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    missing = len(values) - len(valid)
    if not valid:
        return {
            "count": 0,
            "missing": missing,
            "min": None,
            "p01": None,
            "p05": None,
            "mean": None,
            "median": None,
            "p95": None,
            "p99": None,
            "max": None,
            "std": None,
            "iqr": None,
            "outlier_count": 0,
            "whisker_low": None,
            "whisker_high": None,
        }
    q1 = quantile(valid, 0.25)
    q3 = quantile(valid, 0.75)
    iqr = (q3 - q1) if q1 is not None and q3 is not None else None
    low = q1 - 3 * iqr if iqr is not None else None
    high = q3 + 3 * iqr if iqr is not None else None
    outlier_count = sum(1 for v in valid if (low is not None and v < low) or (high is not None and v > high))
    return {
        "count": len(valid),
        "missing": missing,
        "min": min(valid),
        "p01": quantile(valid, 0.01),
        "p05": quantile(valid, 0.05),
        "mean": statistics.fmean(valid),
        "median": statistics.median(valid),
        "p95": quantile(valid, 0.95),
        "p99": quantile(valid, 0.99),
        "max": max(valid),
        "std": statistics.pstdev(valid) if len(valid) > 1 else 0.0,
        "iqr": iqr,
        "outlier_count": outlier_count,
        "whisker_low": low,
        "whisker_high": high,
    }


def categorical_distribution(values: Iterable[Any], total: int | None = None) -> list[dict[str, Any]]:
    vals = [str(v) if v not in {None, ""} else "<missing>" for v in values]
    denom = total if total is not None else len(vals)
    counts = Counter(vals)
    rows = []
    for value, count in counts.most_common():
        rows.append({"value": value, "count": count, "percent": count / denom * 100 if denom else 0.0})
    return rows


def compact_distribution(rows: list[dict[str, Any]], limit: int = 5) -> str:
    parts = []
    for row in rows[:limit]:
        value = row.get("value", "")
        count = row.get("count", 0)
        percent = row.get("percent", 0.0)
        parts.append(f"{value}:{count} ({float(percent):.2f}%)")
    return "; ".join(parts)


def weighted_distribution(values: list[Any], weights: list[float], value_name: str = "value") -> list[dict[str, Any]]:
    totals: defaultdict[str, float] = defaultdict(float)
    for value, weight in zip(values, weights):
        key = str(value) if value not in {None, ""} else "<missing>"
        totals[key] += float(weight or 0.0)
    grand = sum(totals.values())
    return [
        {value_name: key, "hours": hours, "percent_hours": hours / grand * 100 if grand else 0.0}
        for key, hours in sorted(totals.items(), key=lambda x: x[1], reverse=True)
    ]


def histogram_rows(values: list[float], bins: int | str = "auto", prefix: str = "bin") -> list[dict[str, Any]]:
    valid = np.asarray([v for v in values if v is not None and math.isfinite(v)], dtype=float)
    if valid.size == 0:
        return []
    counts, edges = np.histogram(valid, bins=bins)
    total = counts.sum()
    rows = []
    for idx, count in enumerate(counts):
        left = float(edges[idx])
        right = float(edges[idx + 1])
        width = right - left
        density = float(count / total / width) if total and width > 0 else 0.0
        rows.append(
            {
                f"{prefix}_left": left,
                f"{prefix}_right": right,
                f"{prefix}_center": (left + right) / 2,
                "count": int(count),
                "percent": float(count / total * 100) if total else 0.0,
                "density": density,
            }
        )
    return rows


def ecdf_rows(values: list[float]) -> list[dict[str, Any]]:
    valid = sorted(v for v in values if v is not None and math.isfinite(v))
    total = len(valid)
    return [
        {"value": value, "cumulative_count": idx, "cumulative_percent": idx / total * 100 if total else 0.0}
        for idx, value in enumerate(valid, 1)
    ]


def boxplot_summary(metric: str, values: list[float]) -> dict[str, Any]:
    summary = numeric_summary(values)
    return {
        "metric": metric,
        "min": summary["min"],
        "q1": quantile([v for v in values if v is not None], 0.25),
        "median": summary["median"],
        "q3": quantile([v for v in values if v is not None], 0.75),
        "max": summary["max"],
        "iqr": summary["iqr"],
        "whisker_low": summary["whisker_low"],
        "whisker_high": summary["whisker_high"],
        "outlier_count": summary["outlier_count"],
    }


def normalize_channel_name(name: str) -> str:
    out = name.upper().strip()
    out = out.replace("EEG", "").replace("-REF", "").replace("-LE", "")
    out = out.replace(" ", "").replace(".", "").replace("_", "")
    out = out.replace("FPZ", "FPZ")
    aliases = {"T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8"}
    return aliases.get(out, out)


def eeg_channel_names(row: dict[str, str]) -> list[str]:
    names = str(row.get("channel_names", "")).split("|") if row.get("channel_names") else []
    types = str(row.get("channel_types", "")).split("|") if row.get("channel_types") else []
    out = []
    for idx, name in enumerate(names):
        kind = types[idx] if idx < len(types) else ""
        if kind == "eeg" or name.upper().strip().startswith("EEG"):
            normalized = normalize_channel_name(name)
            if normalized:
                out.append(normalized)
    return out


def source_or_subset(row: dict[str, str]) -> str:
    return row.get("top_level_subset") or "<root>"


def duration_hours(row: dict[str, str]) -> float:
    duration = to_float(row.get("duration_sec"))
    return duration / 3600 if duration is not None else 0.0


def write_numeric_artifacts(metric: str, values: list[float], stats_dir: Path, plot_dir: Path, manifest: list[dict[str, Any]], unit: str, log_scale: bool = False) -> None:
    write_csv(stats_dir / f"{metric}_summary.csv", [numeric_summary(values)])
    hist = histogram_rows(values, bins="auto", prefix="bin")
    hist_path = plot_dir / f"{metric}_histogram.csv"
    write_csv(hist_path, hist)
    ecdf_path = plot_dir / f"{metric}_ecdf.csv"
    write_csv(ecdf_path, ecdf_rows(values))
    box_path = plot_dir / f"{metric}_boxplot_summary.csv"
    write_csv(box_path, [boxplot_summary(metric, values)])
    manifest.append(
        {
            "name": f"{metric}_distribution",
            "plot_type": "histogram",
            "source_csv": str(hist_path.name),
            "x_left": "bin_left",
            "x_right": "bin_right",
            "x": "bin_center",
            "y": "count",
            "unit": unit,
            "title": metric.replace("_", " ").title(),
            "x_scale": "linear",
            "output_png": f"{metric}_distribution.png",
        }
    )
    if log_scale:
        log_values = [math.log10(v) for v in values if v is not None and v > 0]
        if log_values:
            log_metric = f"{metric}_log10"
            hist_path = plot_dir / f"{log_metric}_histogram.csv"
            write_csv(hist_path, histogram_rows(log_values, bins="auto", prefix="bin"))
            manifest.append(
                {
                    "name": f"{log_metric}_distribution",
                    "plot_type": "histogram",
                    "source_csv": str(hist_path.name),
                    "x_left": "bin_left",
                    "x_right": "bin_right",
                    "x": "bin_center",
                    "y": "count",
                    "unit": f"log10({unit})",
                    "title": f"Log10 {metric.replace('_', ' ').title()}",
                    "x_scale": "linear",
                    "output_png": f"{log_metric}_distribution.png",
                }
            )


def compute(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.output_dir
    stats_dir = out_dir / "stats_tables"
    plot_data_dir = out_dir / "plot_data"
    stats_dir.mkdir(parents=True, exist_ok=True)
    plot_data_dir.mkdir(parents=True, exist_ok=True)

    inventory_summary = read_json(out_dir / "dataset_inventory_summary.json")
    pair_summary = read_json(out_dir / "pair_summary.json")
    inventory_rows = read_csv_rows(out_dir / "dataset_inventory.csv")
    raw_rows = read_csv_rows(out_dir / "raw_eeg_index.csv")
    pair_rows = read_csv_rows(out_dir / "pair_index.csv")
    manifest: list[dict[str, Any]] = []

    readable_rows = [row for row in raw_rows if row.get("status") in {"OK", "WARN"}]
    durations = [to_float(row.get("duration_sec")) for row in readable_rows]
    durations_valid = [v for v in durations if v is not None]
    hours = [duration_hours(row) for row in readable_rows]
    n_channels = [to_int(row.get("n_channels_total")) for row in readable_rows]
    n_eeg_channels = [to_int(row.get("n_eeg_channels")) for row in readable_rows]
    sfreqs = [to_float(row.get("sampling_frequency_hz_mode")) for row in readable_rows]
    seq_lengths = [to_float(row.get("sequence_length_mode")) for row in readable_rows]

    channel_dist = categorical_distribution(n_eeg_channels)
    write_csv(stats_dir / "channel_count_distribution.csv", channel_dist, ["value", "count", "percent"])
    write_csv(plot_data_dir / "channel_count_distribution.csv", channel_dist, ["value", "count", "percent"])
    manifest.append(
        {
            "name": "channel_count_distribution",
            "plot_type": "bar",
            "source_csv": "channel_count_distribution.csv",
            "x": "value",
            "y": "count",
            "unit": "channels",
            "title": "EEG Channel Count Distribution",
            "x_scale": "categorical",
            "output_png": "channel_count_distribution.png",
        }
    )

    sfreq_value_counts = categorical_distribution([v for v in sfreqs if v is not None])
    write_csv(stats_dir / "sampling_frequency_value_counts.csv", sfreq_value_counts, ["value", "count", "percent"])
    write_numeric_artifacts("sampling_frequency", [v for v in sfreqs if v is not None], stats_dir, plot_data_dir, manifest, "Hz")
    write_numeric_artifacts("duration", durations_valid, stats_dir, plot_data_dir, manifest, "sec", log_scale=True)
    write_numeric_artifacts("sequence_length", [v for v in seq_lengths if v is not None], stats_dir, plot_data_dir, manifest, "samples", log_scale=True)

    format_dist = categorical_distribution([row.get("file_format", "") for row in raw_rows])
    subset_dist = categorical_distribution([source_or_subset(row) for row in raw_rows])
    pair_dist = categorical_distribution([row.get("pair_status", "") for row in pair_rows])
    write_csv(stats_dir / "file_format_distribution.csv", format_dist)
    write_csv(stats_dir / "subset_composition.csv", subset_dist)
    write_csv(stats_dir / "pair_status_distribution.csv", pair_dist)
    write_csv(plot_data_dir / "file_format_distribution.csv", format_dist)
    write_csv(plot_data_dir / "raw_eeg_count_by_subset.csv", subset_dist)
    write_csv(plot_data_dir / "pair_status_distribution.csv", pair_dist)
    for name, title in [
        ("file_format_distribution", "Raw EEG Format Distribution"),
        ("raw_eeg_count_by_subset", "Raw EEG Count By Subset"),
        ("pair_status_distribution", "Pair Status Distribution"),
    ]:
        manifest.append(
            {
                "name": name,
                "plot_type": "bar",
                "source_csv": f"{name}.csv",
                "x": "value",
                "y": "count",
                "unit": "count",
                "title": title,
                "x_scale": "categorical",
                "output_png": f"{name}.png",
            }
        )

    subject_hours: defaultdict[str, float] = defaultdict(float)
    subject_records: Counter[str] = Counter()
    session_records: Counter[str] = Counter()
    for row in readable_rows:
        subject = row.get("inferred_subject_id") or "<missing>"
        session = row.get("inferred_session_id") or "<missing>"
        subject_records[subject] += 1
        subject_hours[subject] += duration_hours(row)
        session_records[session] += 1
    write_csv(
        stats_dir / "subject_recording_distribution.csv",
        [
            {"subject_id": subject, "recordings": count, "hours": subject_hours[subject]}
            for subject, count in subject_records.most_common()
        ],
    )
    write_csv(
        stats_dir / "subject_hour_distribution.csv",
        [{"subject_id": subject, "hours": hours} for subject, hours in sorted(subject_hours.items(), key=lambda x: x[1], reverse=True)],
    )
    write_csv(stats_dir / "session_distribution.csv", [{"session_id": key, "recordings": val} for key, val in session_records.most_common()])
    subject_hour_values = list(subject_hours.values())
    if subject_hour_values:
        write_csv(plot_data_dir / "subject_hours_histogram.csv", histogram_rows(subject_hour_values, prefix="bin"))
        manifest.append(
            {
                "name": "subject_hours_histogram",
                "plot_type": "histogram",
                "source_csv": "subject_hours_histogram.csv",
                "x": "bin_center",
                "y": "count",
                "unit": "hours",
                "title": "Hours Per Subject",
                "x_scale": "linear",
                "output_png": "subject_hours_histogram.png",
            }
        )

    source_hours = weighted_distribution([source_or_subset(row) for row in readable_rows], hours, value_name="source")
    write_csv(stats_dir / "source_scale_summary.csv", source_hours)
    write_csv(plot_data_dir / "source_hour_distribution.csv", source_hours)
    manifest.append(
        {
            "name": "source_hour_distribution",
            "plot_type": "bar",
            "source_csv": "source_hour_distribution.csv",
            "x": "source",
            "y": "hours",
            "unit": "hours",
            "title": "Hours By Source Or Subset",
            "x_scale": "categorical",
            "output_png": "source_hour_distribution.png",
        }
    )

    channel_counter: Counter[str] = Counter()
    channel_set_counter: Counter[str] = Counter()
    coverage_rows: list[dict[str, Any]] = []
    missing_rows: list[dict[str, Any]] = []
    for row in readable_rows:
        channels = set(eeg_channel_names(row))
        for ch in channels:
            channel_counter[ch] += 1
        pattern = "|".join(sorted(channels)) if channels else "<missing>"
        channel_set_counter[pattern] += 1
        for set_name, canonical in CANONICAL_CHANNEL_SETS.items():
            present = len(channels & canonical)
            coverage = present / len(canonical) * 100 if canonical else 0.0
            coverage_rows.append(
                {
                    "relative_path": row.get("relative_path", ""),
                    "canonical_set": set_name,
                    "present_channels": present,
                    "required_channels": len(canonical),
                    "coverage_percent": coverage,
                    "missing_count": len(canonical - channels),
                }
            )
            if len(missing_rows) < 250000:
                missing_rows.append(
                    {
                        "relative_path": row.get("relative_path", ""),
                        "canonical_set": set_name,
                        "missing_channels": "|".join(sorted(canonical - channels)),
                    }
                )
    write_csv(stats_dir / "channel_name_frequency.csv", [{"channel_name": k, "recordings": v} for k, v in channel_counter.most_common()])
    write_csv(
        stats_dir / "channel_set_patterns.csv",
        [{"channel_set": k, "recordings": v, "percent": v / len(readable_rows) * 100 if readable_rows else 0.0} for k, v in channel_set_counter.most_common()],
    )
    write_csv(stats_dir / "canonical_channel_coverage.csv", coverage_rows)
    write_csv(stats_dir / "missing_channel_matrix.csv", missing_rows)
    coverage_by_set: defaultdict[str, list[float]] = defaultdict(list)
    for row in coverage_rows:
        coverage_by_set[row["canonical_set"]].append(float(row["coverage_percent"]))
    write_csv(
        plot_data_dir / "channel_coverage_heatmap.csv",
        [
            {"canonical_set": key, "mean_coverage_percent": statistics.fmean(vals), "median_coverage_percent": statistics.median(vals)}
            for key, vals in sorted(coverage_by_set.items())
        ],
    )

    resample_rows = []
    total_hours = sum(hours)
    for target in RESAMPLE_TARGETS:
        at_or_above = sum(duration_hours(row) for row in readable_rows if (to_float(row.get("sampling_frequency_hz_mode")) or 0) >= target)
        below = total_hours - at_or_above
        resample_rows.append(
            {
                "target_hz": target,
                "hours_at_or_above_target": at_or_above,
                "percent_hours_at_or_above_target": at_or_above / total_hours * 100 if total_hours else 0.0,
                "hours_below_target": below,
                "percent_hours_below_target": below / total_hours * 100 if total_hours else 0.0,
            }
        )
    write_csv(stats_dir / "resample_target_candidates.csv", resample_rows)

    window_rows = []
    for window in WINDOW_SECONDS:
        total_windows = 0
        total_tail = 0.0
        usable_records = 0
        for duration in durations_valid:
            windows = int(duration // window)
            if windows > 0:
                usable_records += 1
            total_windows += windows
            total_tail += duration - windows * window
        window_rows.append(
            {
                "window_sec": window,
                "total_nonoverlap_windows": total_windows,
                "usable_recordings": usable_records,
                "percent_usable_recordings": usable_records / len(durations_valid) * 100 if durations_valid else 0.0,
                "discarded_tail_sec": total_tail,
            }
        )
    write_csv(stats_dir / "window_feasibility.csv", window_rows)
    write_csv(plot_data_dir / "window_count_by_duration.csv", window_rows)

    token_rows = []
    for patch in PATCH_SECONDS:
        token_counts = []
        for row in readable_rows:
            duration = to_float(row.get("duration_sec"))
            channels = to_int(row.get("n_eeg_channels"))
            if duration is None or channels is None:
                continue
            token_counts.append(channels * math.ceil(duration / patch))
        summary = numeric_summary(token_counts)
        token_rows.append({"patch_duration_sec": patch, **summary})
    write_csv(stats_dir / "patch_token_budget.csv", token_rows)

    metadata_fields = Counter()
    metadata_missing = Counter()
    metadata_rows = [row for row in inventory_rows if row.get("file_role") in {"metadata", "annotation", "text_report"}]
    for row in metadata_rows:
        metadata_fields[row.get("suffix", "<missing>")] += 1
        if not row.get("inferred_subject_id"):
            metadata_missing["missing_subject_id"] += 1
    write_csv(stats_dir / "metadata_field_coverage.csv", [{"field_or_suffix": k, "count": v} for k, v in metadata_fields.most_common()])
    write_csv(stats_dir / "label_file_coverage.csv", categorical_distribution([row.get("suffix", "") for row in metadata_rows]))

    duplicate_counter: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
    version_counter: defaultdict[str, set[str]] = defaultdict(set)
    for row in inventory_rows:
        rel = row.get("relative_path", "")
        path = Path(rel)
        size = row.get("size_bytes", "")
        duplicate_counter[(path.stem, str(size))].append(rel)
        for part in path.parts:
            if part.startswith("v") and any(ch.isdigit() for ch in part):
                version_counter[path.stem].add(part)
    duplicate_rows = [
        {"stem": stem, "size_bytes": size, "file_count": len(paths), "paths": "|".join(paths[:50])}
        for (stem, size), paths in duplicate_counter.items()
        if len(paths) > 1
    ]
    write_csv(stats_dir / "duplicate_file_candidates.csv", duplicate_rows)
    write_csv(
        stats_dir / "version_overlap_summary.csv",
        [{"stem": stem, "version_count": len(versions), "versions": "|".join(sorted(versions))} for stem, versions in version_counter.items() if len(versions) > 1],
    )

    n_channels_summary = numeric_summary(n_channels)
    n_eeg_channels_summary = numeric_summary(n_eeg_channels)
    sfreq_summary = numeric_summary(sfreqs)
    duration_summary = numeric_summary(durations)
    sequence_summary = numeric_summary(seq_lengths)
    reader_errors = sum(1 for row in raw_rows if row.get("status") == "ERROR")
    annotation_pair_count = sum(1 for row in pair_rows if row.get("annotation_pairs"))
    exact_pair_count = sum(1 for row in pair_rows if row.get("exact_stem_pairs"))
    paired_any = pair_summary.get("paired_any_count", 0)
    total_size_bytes = int(inventory_summary.get("total_size_bytes") or 0)
    raw_header_size_bytes = int(inventory_summary.get("raw_eeg_size_bytes") or 0)
    raw_data_size_bytes = int(inventory_summary.get("raw_data_size_bytes") or raw_header_size_bytes)
    presentation_row = {
        "dataset_name": args.dataset_name,
        "input_root": inventory_summary.get("input_root", ""),
        "total_size_gb": total_size_bytes / 1_000_000_000,
        "raw_eeg_header_size_gb": raw_header_size_bytes / 1_000_000_000,
        "raw_data_size_gb": raw_data_size_bytes / 1_000_000_000,
        "total_files": inventory_summary.get("total_files", 0),
        "raw_recordings": len(raw_rows),
        "readable_raw_recordings": len(readable_rows),
        "reader_error_count": reader_errors,
        "inferred_subject_count": inventory_summary.get("inferred_subject_count", 0),
        "inferred_session_count": inventory_summary.get("inferred_session_count", 0),
        "total_raw_eeg_hours": total_hours,
        "raw_formats": compact_distribution(format_dist),
        "top_level_subsets": compact_distribution(subset_dist),
        "dominant_sampling_frequencies_hz": compact_distribution(sfreq_value_counts),
        "n_eeg_channels_median": n_eeg_channels_summary["median"],
        "n_eeg_channels_min": n_eeg_channels_summary["min"],
        "n_eeg_channels_max": n_eeg_channels_summary["max"],
        "recording_duration_sec_median": duration_summary["median"],
        "recording_duration_sec_mean": duration_summary["mean"],
        "sequence_length_samples_median": sequence_summary["median"],
        "sequence_length_samples_mean": sequence_summary["mean"],
        "estimated_30s_windows": next((row["total_nonoverlap_windows"] for row in window_rows if row["window_sec"] == 30), ""),
        "recommended_window_lengths_sec": "|".join(str(row["window_sec"]) for row in window_rows if row["percent_usable_recordings"] >= 95),
        "pair_status_summary": compact_distribution(pair_dist),
        "paired_any_count": paired_any,
        "paired_any_percent": paired_any / len(pair_rows) * 100 if pair_rows else 0.0,
        "exact_stem_pair_count": exact_pair_count,
        "annotation_pair_count": annotation_pair_count,
        "annotation_pair_percent": annotation_pair_count / len(pair_rows) * 100 if pair_rows else 0.0,
    }
    write_csv(stats_dir / "dataset_presentation_summary.csv", [presentation_row])

    stats_summary = {
        "dataset_name": args.dataset_name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "inventory": inventory_summary,
        "pairing": pair_summary,
        "raw_eeg_files": len(raw_rows),
        "readable_raw_eeg_files": len(readable_rows),
        "reader_error_count": reader_errors,
        "total_raw_eeg_hours": total_hours,
        "dataset_presentation_summary": presentation_row,
        "n_channels_total": n_channels_summary,
        "n_eeg_channels": n_eeg_channels_summary,
        "sampling_frequency_hz": sfreq_summary,
        "duration_sec": duration_summary,
        "sequence_length_samples": sequence_summary,
        "quality_flags": {
            "missing_metadata_count": sum(1 for row in raw_rows if not row.get("inferred_subject_id")),
            "zero_duration_count": sum(1 for v in durations if v == 0),
            "duration_outlier_count": duration_summary["outlier_count"],
            "sampling_frequency_outlier_count": sfreq_summary["outlier_count"],
            "channel_count_outlier_count": n_eeg_channels_summary["outlier_count"],
            "sequence_length_outlier_count": sequence_summary["outlier_count"],
            "reader_error_count": reader_errors,
        },
        "pretraining_readiness": {
            "dominant_sampling_frequencies_hz": [row["value"] for row in sfreq_value_counts[:5]],
            "recommended_resample_targets_hz": [
                row["target_hz"] for row in resample_rows if row["percent_hours_at_or_above_target"] >= 90
            ],
            "dominant_channel_counts": [row["value"] for row in channel_dist[:5]],
            "candidate_canonical_channel_sets": [
                key for key, vals in sorted(coverage_by_set.items()) if vals and statistics.fmean(vals) >= 80
            ],
            "recommended_window_lengths_sec": [
                row["window_sec"] for row in window_rows if row["percent_usable_recordings"] >= 95
            ],
            "estimated_total_train_windows": {str(row["window_sec"]): row["total_nonoverlap_windows"] for row in window_rows},
            "duration_chunking_needed": any((v or 0) > 60 for v in durations),
            "major_quality_risks": [],
            "major_balance_risks": [],
            "major_pairing_risks": [] if pair_summary.get("paired_any_count", 0) else ["no_structural_pairs_detected"],
            "notes": [
                "Deep signal quality metrics are only available when a future payload-level scan is enabled.",
                "Structural pairing does not validate label semantics.",
            ],
        },
    }

    (out_dir / "statistics_summary.json").write_text(
        json.dumps(stats_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (plot_data_dir / "plot_manifest.json").write_text(
        json.dumps({"plots": manifest}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return stats_summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compute EEG dataset statistics tables and plot data.")
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = compute(args)
    print(f"[INFO] Raw EEG files: {summary['raw_eeg_files']}")
    print(f"[INFO] Total raw EEG hours: {summary['total_raw_eeg_hours']:.3f}")
    print(f"[INFO] Statistics written to: {args.output_dir / 'statistics_summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
