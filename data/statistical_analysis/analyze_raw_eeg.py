#!/usr/bin/env python3
"""Analyze raw EEG metadata and structural pairing for a dataset inventory."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from subject_inference import infer_from_relative_path, top_level_subset


DEFAULT_RAW_FORMATS = {
    ".edf",
    ".bdf",
    ".gdf",
    ".vhdr",
    ".set",
    ".fif",
    ".fif.gz",
    ".cnt",
    ".mff",
    ".hea",
}
PAIR_SUFFIXES = {
    ".json",
    ".tsv",
    ".csv",
    ".tse",
    ".lbl",
    ".rec",
    ".vmrk",
    ".txt",
    ".edf.seizures",
    ".ann",
}
ANNOTATION_SUFFIXES = {".tse", ".lbl", ".rec", ".vmrk", ".edf.seizures", ".ann"}
TEXT_SUFFIXES = {".txt", ".md"}
GLOBAL_METADATA_NAMES = {
    "dataset_description.json",
    "participants.tsv",
    "participants.json",
    "sessions.tsv",
    "README",
    "README.md",
    "CHANGES",
    "CHANGES.md",
}


def suffix_key(path: Path | str) -> str:
    name = Path(path).name.lower()
    if name.endswith(".fif.gz"):
        return ".fif.gz"
    if name.endswith(".edf.seizures"):
        return ".edf.seizures"
    return Path(name).suffix.lower() or "<no_suffix>"


def parse_raw_formats(value: str) -> set[str]:
    if not value:
        return set(DEFAULT_RAW_FORMATS)
    out = set()
    for item in value.split(","):
        item = item.strip().lower()
        if not item:
            continue
        out.add(item if item.startswith(".") else f".{item}")
    return out


def safe_decode(raw: bytes) -> str:
    return raw.decode("latin1", errors="replace").replace("\x00", "").strip()


def parse_int(value: str, default: int | None = None) -> int | None:
    try:
        return int(value.strip())
    except Exception:
        return default


def parse_float(value: str, default: float | None = None) -> float | None:
    try:
        return float(value.strip())
    except Exception:
        return default


def read_fixed_fields(blob: bytes, ns: int, width: int, offset: int) -> tuple[list[str], int]:
    values = [safe_decode(blob[offset + i * width : offset + (i + 1) * width]) for i in range(ns)]
    return values, offset + ns * width


def channel_type(label: str, unit: str = "") -> str:
    raw = label.strip()
    name = raw.upper()
    normalized_unit = unit.strip().lower().replace("μ", "u").replace("µ", "u")
    if "ANNOT" in name or name in {"STATUS", "TRIGGER", "EVENT", "EDF ANNOTATIONS"}:
        return "annotation"
    if name.startswith("EEG ") or normalized_unit in {"uv", "microv", "microvolt", "microvolts"}:
        if any(token in name for token in ("EOG", "ECG", "EKG", "EMG", "RESP", "PHOTIC")):
            return "aux"
        return "eeg"
    if "EOG" in name:
        return "eog"
    if "ECG" in name or "EKG" in name:
        return "ecg"
    if "EMG" in name:
        return "emg"
    if "RESP" in name:
        return "resp"
    return "misc"


def mode_float(values: list[float]) -> float | None:
    if not values:
        return None
    rounded = [round(float(v), 6) for v in values if math.isfinite(float(v))]
    if not rounded:
        return None
    return Counter(rounded).most_common(1)[0][0]


def mode_int(values: list[int]) -> int | None:
    vals = [int(v) for v in values]
    if not vals:
        return None
    return Counter(vals).most_common(1)[0][0]


def join_values(values: list[Any]) -> str:
    return "|".join("" if v is None else str(v) for v in values)


def read_edf_like_header(path: Path, file_format: str) -> dict[str, Any]:
    size_bytes = path.stat().st_size
    with path.open("rb") as f:
        fixed = f.read(256)
        if len(fixed) != 256:
            raise ValueError("file shorter than EDF/BDF fixed header")
        header_bytes = parse_int(safe_decode(fixed[184:192]))
        n_records = parse_int(safe_decode(fixed[236:244]))
        record_duration = parse_float(safe_decode(fixed[244:252]))
        n_channels = parse_int(safe_decode(fixed[252:256]))
        start_date = safe_decode(fixed[168:176])
        start_time = safe_decode(fixed[176:184])

        if not header_bytes or not n_channels or n_channels <= 0:
            raise ValueError("invalid header_bytes or n_channels")
        rest = f.read(header_bytes - 256)
        if len(rest) != header_bytes - 256:
            raise ValueError("file shorter than EDF/BDF signal header")

    off = 0
    labels, off = read_fixed_fields(rest, n_channels, 16, off)
    _transducers, off = read_fixed_fields(rest, n_channels, 80, off)
    phys_dims, off = read_fixed_fields(rest, n_channels, 8, off)
    _phys_min, off = read_fixed_fields(rest, n_channels, 8, off)
    _phys_max, off = read_fixed_fields(rest, n_channels, 8, off)
    _dig_min, off = read_fixed_fields(rest, n_channels, 8, off)
    _dig_max, off = read_fixed_fields(rest, n_channels, 8, off)
    _prefilters, off = read_fixed_fields(rest, n_channels, 80, off)
    samples_s, off = read_fixed_fields(rest, n_channels, 8, off)
    samples_per_record = [parse_int(x, 0) or 0 for x in samples_s]
    ch_types = [channel_type(label, unit) for label, unit in zip(labels, phys_dims)]
    eeg_indices = [i for i, kind in enumerate(ch_types) if kind == "eeg"]
    use_indices = eeg_indices if eeg_indices else list(range(n_channels))

    sfreqs: list[float] = []
    sequence_lengths: list[int] = []
    warnings: list[str] = []
    duration_sec: float | None = None
    if n_records is None or n_records < 0:
        warnings.append("unknown_or_negative_data_record_count")
    if not record_duration or record_duration <= 0:
        warnings.append("invalid_record_duration")
    if n_records is not None and n_records >= 0 and record_duration and record_duration > 0:
        duration_sec = n_records * record_duration
        for idx in use_indices:
            samples = samples_per_record[idx]
            if samples > 0:
                sfreqs.append(samples / record_duration)
                sequence_lengths.append(samples * n_records)

    bytes_per_sample = 3 if file_format == "bdf" else 2
    expected_size = None
    if n_records is not None and n_records >= 0:
        expected_size = header_bytes + n_records * sum(samples_per_record) * bytes_per_sample
        if expected_size != size_bytes:
            warnings.append(f"size_delta_bytes={size_bytes - expected_size}")

    status = "OK" if not warnings else "WARN"
    return {
        "status": status,
        "error": "",
        "format": file_format,
        "n_channels_total": n_channels,
        "n_eeg_channels": len(eeg_indices),
        "channel_names": labels,
        "channel_types": ch_types,
        "sampling_frequency_hz_min": min(sfreqs) if sfreqs else None,
        "sampling_frequency_hz_max": max(sfreqs) if sfreqs else None,
        "sampling_frequency_hz_mode": mode_float(sfreqs),
        "duration_sec": duration_sec,
        "sequence_length_min": min(sequence_lengths) if sequence_lengths else None,
        "sequence_length_max": max(sequence_lengths) if sequence_lengths else None,
        "sequence_length_mode": mode_int(sequence_lengths),
        "physical_units": sorted(set(x for x in phys_dims if x)),
        "start_datetime": f"{start_date} {start_time}".strip(),
        "header_warnings": warnings,
    }


def read_wfdb_header(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="latin1", errors="replace").splitlines()
    content = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not content:
        raise ValueError("empty WFDB header")

    first = content[0].split()
    if len(first) < 3:
        raise ValueError("invalid WFDB record header")
    n_signals = int(first[1])
    fs_token = first[2].split("/")[0]
    sfreq = float(fs_token)
    signal_length = int(first[3]) if len(first) >= 4 and first[3].lstrip("-").isdigit() else None
    duration_sec = signal_length / sfreq if signal_length is not None and sfreq > 0 else None

    labels: list[str] = []
    physical_units: list[str] = []
    for line in content[1 : 1 + n_signals]:
        fields = line.split()
        label = " ".join(fields[8:]) if len(fields) > 8 else (fields[-1] if fields else "")
        labels.append(label)
        gain_field = fields[2] if len(fields) > 2 else ""
        unit = ""
        if "/" in gain_field:
            unit = gain_field.split("/", 1)[1]
        physical_units.append(unit)

    while len(labels) < n_signals:
        labels.append(f"signal_{len(labels)}")
        physical_units.append("")

    ch_types = [channel_type(label, unit) for label, unit in zip(labels, physical_units)]
    eeg_count = sum(1 for kind in ch_types if kind == "eeg")
    warnings: list[str] = []
    if signal_length is None:
        warnings.append("missing_signal_length")
    if sfreq <= 0:
        warnings.append("invalid_sampling_frequency")

    return {
        "status": "WARN" if warnings else "OK",
        "error": "",
        "format": "wfdb",
        "n_channels_total": n_signals,
        "n_eeg_channels": eeg_count,
        "channel_names": labels,
        "channel_types": ch_types,
        "sampling_frequency_hz_min": sfreq,
        "sampling_frequency_hz_max": sfreq,
        "sampling_frequency_hz_mode": sfreq,
        "duration_sec": duration_sec,
        "sequence_length_min": signal_length,
        "sequence_length_max": signal_length,
        "sequence_length_mode": signal_length,
        "physical_units": sorted(set(x for x in physical_units if x)),
        "start_datetime": "",
        "header_warnings": warnings,
    }


def read_mne_header(path: Path, file_format: str) -> dict[str, Any]:
    try:
        import mne
    except Exception as exc:  # pragma: no cover - dependency-dependent
        raise RuntimeError(f"MNE is required for {file_format} metadata: {exc}") from exc

    readers = {
        ".vhdr": mne.io.read_raw_brainvision,
        ".set": mne.io.read_raw_eeglab,
        ".fif": mne.io.read_raw_fif,
        ".fif.gz": mne.io.read_raw_fif,
        ".gdf": mne.io.read_raw_gdf,
        ".cnt": mne.io.read_raw_cnt,
        ".mff": mne.io.read_raw_egi,
    }
    reader = readers.get(file_format)
    if reader is None:
        raise RuntimeError(f"no MNE reader configured for {file_format}")
    raw = reader(str(path), preload=False, verbose="ERROR")
    ch_names = list(raw.ch_names)
    ch_types = list(raw.get_channel_types())
    sfreq = float(raw.info["sfreq"])
    n_times = int(raw.n_times)
    duration_sec = n_times / sfreq if sfreq > 0 else None
    eeg_count = sum(1 for x in ch_types if x == "eeg")
    return {
        "status": "OK",
        "error": "",
        "format": file_format.lstrip("."),
        "n_channels_total": len(ch_names),
        "n_eeg_channels": eeg_count,
        "channel_names": ch_names,
        "channel_types": ch_types,
        "sampling_frequency_hz_min": sfreq,
        "sampling_frequency_hz_max": sfreq,
        "sampling_frequency_hz_mode": sfreq,
        "duration_sec": duration_sec,
        "sequence_length_min": n_times,
        "sequence_length_max": n_times,
        "sequence_length_mode": n_times,
        "physical_units": [],
        "start_datetime": str(raw.info.get("meas_date") or ""),
        "header_warnings": [],
    }


def read_raw_eeg_metadata(path: Path, suffix: str) -> dict[str, Any]:
    try:
        if suffix == ".edf":
            return read_edf_like_header(path, "edf")
        if suffix == ".bdf":
            return read_edf_like_header(path, "bdf")
        if suffix == ".hea":
            return read_wfdb_header(path)
        return read_mne_header(path, suffix)
    except Exception as exc:
        return {
            "status": "ERROR",
            "error": str(exc),
            "format": suffix.lstrip("."),
            "n_channels_total": None,
            "n_eeg_channels": None,
            "channel_names": [],
            "channel_types": [],
            "sampling_frequency_hz_min": None,
            "sampling_frequency_hz_max": None,
            "sampling_frequency_hz_mode": None,
            "duration_sec": None,
            "sequence_length_min": None,
            "sequence_length_max": None,
            "sequence_length_mode": None,
            "physical_units": [],
            "start_datetime": "",
            "header_warnings": [],
        }


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_pair_context(inventory_rows: list[dict[str, str]]) -> dict[str, Any]:
    by_dir_stem: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    by_dir: dict[str, list[dict[str, str]]] = defaultdict(list)
    global_metadata: list[str] = []
    for row in inventory_rows:
        rel = row["relative_path"]
        path = Path(rel)
        key = (str(path.parent), pair_stem(path))
        by_dir_stem[key].append(row)
        by_dir[str(path.parent)].append(row)
        if path.name in GLOBAL_METADATA_NAMES or path.name.lower() in {x.lower() for x in GLOBAL_METADATA_NAMES}:
            global_metadata.append(rel)
    return {"by_dir_stem": by_dir_stem, "by_dir": by_dir, "global_metadata": sorted(set(global_metadata))}


def pair_stem(path: Path | str) -> str:
    p = Path(path)
    name = p.name
    lower = name.lower()
    for compound_suffix in (".edf.seizures", ".fif.gz"):
        if lower.endswith(compound_suffix):
            return name[: -len(compound_suffix)]
    return p.stem


def ancestors(path: Path) -> list[str]:
    dirs = []
    parent = path.parent
    while str(parent) not in {"", "."}:
        dirs.append(str(parent))
        parent = parent.parent
    dirs.append(".")
    return dirs


def analyze_pairing(raw_row: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    rel = raw_row["relative_path"]
    raw_path = Path(rel)
    raw_suffix = suffix_key(raw_path)
    same_stem = context["by_dir_stem"].get((str(raw_path.parent), pair_stem(raw_path)), [])
    exact_pairs: list[str] = []
    annotation_pairs: list[str] = []
    text_pairs: list[str] = []
    inherited_pairs: list[str] = []

    for row in same_stem:
        candidate = row["relative_path"]
        if candidate == rel:
            continue
        suffix = row.get("suffix") or suffix_key(candidate)
        if suffix in PAIR_SUFFIXES:
            exact_pairs.append(candidate)
        if suffix in ANNOTATION_SUFFIXES:
            annotation_pairs.append(candidate)
        if suffix in TEXT_SUFFIXES:
            text_pairs.append(candidate)

    for dir_name in ancestors(raw_path):
        for row in context["by_dir"].get(dir_name, []):
            name = Path(row["relative_path"]).name
            lower = name.lower()
            if (
                lower in {"participants.tsv", "sessions.tsv", "dataset_description.json"}
                or lower.endswith("_scans.tsv")
                or lower.endswith("_events.tsv")
                or lower.endswith("_eeg.json")
                or lower.endswith("_channels.tsv")
                or lower.endswith("_electrodes.tsv")
            ):
                inherited_pairs.append(row["relative_path"])

    global_pairs = context["global_metadata"]
    pair_types: list[str] = []
    if exact_pairs:
        pair_types.append("exact_stem")
    if inherited_pairs:
        pair_types.append("inherited_metadata")
    if annotation_pairs:
        pair_types.append("annotation")
    if text_pairs:
        pair_types.append("text_report")
    if global_pairs:
        pair_types.append("global_metadata")

    if annotation_pairs and (inherited_pairs or global_pairs or exact_pairs):
        pair_status = "rich_pair"
    elif annotation_pairs:
        pair_status = "annotation_or_label"
    elif inherited_pairs or exact_pairs:
        pair_status = "metadata_only"
    elif global_pairs:
        pair_status = "global_only"
    else:
        pair_status = "none"

    all_pairs = sorted(set(exact_pairs + inherited_pairs + annotation_pairs + text_pairs + global_pairs))
    return {
        "raw_relative_path": rel,
        "raw_suffix": raw_suffix,
        "inferred_subject_id": raw_row.get("inferred_subject_id", ""),
        "inferred_session_id": raw_row.get("inferred_session_id", ""),
        "paired_file_count": len(all_pairs),
        "pair_types": join_values(pair_types),
        "exact_stem_pairs": join_values(sorted(set(exact_pairs))),
        "inherited_metadata_pairs": join_values(sorted(set(inherited_pairs))),
        "annotation_pairs": join_values(sorted(set(annotation_pairs))),
        "text_report_pairs": join_values(sorted(set(text_pairs))),
        "global_metadata_pairs": join_values(sorted(set(global_pairs))),
        "pair_score": len(all_pairs),
        "pair_status": pair_status,
    }


def analyze_one(raw_inv_row: dict[str, str]) -> dict[str, Any]:
    abs_path = Path(raw_inv_row["path"])
    rel = raw_inv_row["relative_path"]
    suffix = raw_inv_row.get("suffix") or suffix_key(abs_path)
    inferred = infer_from_relative_path(rel)
    meta = read_raw_eeg_metadata(abs_path, suffix)
    row = {
        "dataset_name": "",
        "relative_path": rel,
        "absolute_path": str(abs_path),
        "file_format": meta["format"],
        "size_bytes": raw_inv_row.get("size_bytes", ""),
        "status": meta["status"],
        "error": meta["error"],
        "inferred_subject_id": raw_inv_row.get("inferred_subject_id") or inferred["inferred_subject_id"],
        "inferred_session_id": raw_inv_row.get("inferred_session_id") or inferred["inferred_session_id"],
        "inferred_task_id": raw_inv_row.get("inferred_task_id") or inferred["inferred_task_id"],
        "inferred_run_id": raw_inv_row.get("inferred_run_id") or inferred["inferred_run_id"],
        "top_level_subset": raw_inv_row.get("top_level_subset") or top_level_subset(rel),
        "n_channels_total": meta["n_channels_total"],
        "n_eeg_channels": meta["n_eeg_channels"],
        "channel_names": join_values(meta["channel_names"]),
        "channel_types": join_values(meta["channel_types"]),
        "sampling_frequency_hz_min": meta["sampling_frequency_hz_min"],
        "sampling_frequency_hz_max": meta["sampling_frequency_hz_max"],
        "sampling_frequency_hz_mode": meta["sampling_frequency_hz_mode"],
        "duration_sec": meta["duration_sec"],
        "sequence_length_min": meta["sequence_length_min"],
        "sequence_length_max": meta["sequence_length_max"],
        "sequence_length_mode": meta["sequence_length_mode"],
        "physical_units": join_values(meta["physical_units"]),
        "start_datetime": meta["start_datetime"],
        "header_warnings": join_values(meta["header_warnings"]),
    }
    return row


def summarize_pairs(pair_rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(str(row["pair_status"]) for row in pair_rows)
    type_counts: Counter[str] = Counter()
    for row in pair_rows:
        for item in str(row.get("pair_types", "")).split("|"):
            if item:
                type_counts[item] += 1
    total = len(pair_rows)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_eeg_files": total,
        "pair_status_counts": dict(sorted(status_counts.items())),
        "pair_status_percent": {
            key: (count / total * 100 if total else 0.0) for key, count in sorted(status_counts.items())
        },
        "pair_type_counts": dict(sorted(type_counts.items())),
        "paired_any_count": sum(1 for row in pair_rows if row["pair_status"] != "none"),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Analyze raw EEG metadata and structural pairing.")
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--inventory-csv", type=Path, required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--raw-formats", default=",".join(sorted(DEFAULT_RAW_FORMATS)))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-files", type=int, default=0)
    parser.add_argument("--deep-signal-scan", action="store_true", help="Reserved for future payload-level signal stats")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rows = read_csv_rows(args.inventory_csv)
    raw_formats = parse_raw_formats(args.raw_formats)
    raw_rows = [
        row
        for row in rows
        if row.get("file_role") == "raw_eeg" and (row.get("suffix") or suffix_key(row["relative_path"])) in raw_formats
    ]
    if args.max_files and args.max_files > 0:
        raw_rows = raw_rows[: args.max_files]

    print(f"[INFO] Raw EEG files selected: {len(raw_rows)}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    analyzed: list[dict[str, Any]] = []
    workers = max(1, args.workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(analyze_one, row) for row in raw_rows]
        for idx, future in enumerate(as_completed(futures), 1):
            row = future.result()
            row["dataset_name"] = args.dataset_name
            analyzed.append(row)
            if idx % 1000 == 0 or idx == len(futures):
                print(f"[INFO] analyzed {idx}/{len(futures)} raw EEG files")

    analyzed.sort(key=lambda x: x["relative_path"])
    raw_fieldnames = [
        "dataset_name",
        "relative_path",
        "absolute_path",
        "file_format",
        "size_bytes",
        "status",
        "error",
        "inferred_subject_id",
        "inferred_session_id",
        "inferred_task_id",
        "inferred_run_id",
        "top_level_subset",
        "n_channels_total",
        "n_eeg_channels",
        "channel_names",
        "channel_types",
        "sampling_frequency_hz_min",
        "sampling_frequency_hz_max",
        "sampling_frequency_hz_mode",
        "duration_sec",
        "sequence_length_min",
        "sequence_length_max",
        "sequence_length_mode",
        "physical_units",
        "start_datetime",
        "header_warnings",
    ]
    write_csv(args.output_dir / "raw_eeg_index.csv", analyzed, raw_fieldnames)
    write_csv(args.output_dir / "raw_eeg_errors.csv", [x for x in analyzed if x["status"] == "ERROR"], raw_fieldnames)

    context = build_pair_context(rows)
    pair_rows = [analyze_pairing(row, context) for row in analyzed]
    pair_fieldnames = [
        "raw_relative_path",
        "raw_suffix",
        "inferred_subject_id",
        "inferred_session_id",
        "paired_file_count",
        "pair_types",
        "exact_stem_pairs",
        "inherited_metadata_pairs",
        "annotation_pairs",
        "text_report_pairs",
        "global_metadata_pairs",
        "pair_score",
        "pair_status",
    ]
    write_csv(args.output_dir / "pair_index.csv", pair_rows, pair_fieldnames)
    (args.output_dir / "pair_summary.json").write_text(
        json.dumps(summarize_pairs(pair_rows), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[INFO] Reader errors: {sum(1 for row in analyzed if row['status'] == 'ERROR')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
