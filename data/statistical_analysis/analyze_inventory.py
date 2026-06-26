#!/usr/bin/env python3
"""Build a file inventory for an EEG dataset directory."""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

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
}
RAW_COMPONENT_SUFFIXES = {".eeg", ".dat", ".fdt"}
METADATA_SUFFIXES = {".json", ".tsv", ".csv", ".xlsx", ".xls", ".mat", ".yaml", ".yml"}
ANNOTATION_SUFFIXES = {".tse", ".lbl", ".rec", ".vmrk", ".evt", ".edf.seizures", ".ann"}
TEXT_SUFFIXES = {".txt", ".md", ".rst"}


def suffix_key(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".fif.gz"):
        return ".fif.gz"
    if name.endswith(".edf.seizures"):
        return ".edf.seizures"
    return path.suffix.lower() or "<no_suffix>"


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


def infer_file_role(path: Path, raw_formats: set[str]) -> str:
    suffix = suffix_key(path)
    name = path.name.lower()
    if suffix in raw_formats:
        return "raw_eeg"
    if suffix in RAW_COMPONENT_SUFFIXES:
        return "raw_eeg_component"
    if suffix in ANNOTATION_SUFFIXES or "annotation" in name or "event" in name or "seizure" in name:
        return "annotation"
    if suffix in METADATA_SUFFIXES:
        return "metadata"
    if suffix in TEXT_SUFFIXES or "report" in name or "readme" in name:
        return "text_report"
    return "image_or_other"


def iter_files(root: Path, follow_symlinks: bool) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        dirnames.sort()
        for filename in sorted(filenames):
            yield Path(dirpath) / filename


def build_inventory(args: argparse.Namespace) -> tuple[list[dict[str, object]], dict[str, object]]:
    root = args.input_root.resolve()
    raw_formats = parse_raw_formats(args.raw_formats)
    rows: list[dict[str, object]] = []
    suffix_counts: Counter[str] = Counter()
    suffix_sizes: Counter[str] = Counter()
    subset_counts: Counter[str] = Counter()
    subset_raw_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    subjects: set[str] = set()
    sessions: set[str] = set()
    tasks: set[str] = set()
    total_size = 0
    raw_size = 0

    for idx, path in enumerate(iter_files(root, args.follow_symlinks), 1):
        if args.max_files and idx > args.max_files:
            break
        try:
            stat = path.stat()
        except OSError as exc:
            rel = str(path.relative_to(root)) if path.is_relative_to(root) else str(path)
            rows.append(
                {
                    "path": str(path),
                    "relative_path": rel,
                    "top_level_subset": top_level_subset(rel),
                    "parent_dir": str(Path(rel).parent),
                    "suffix": suffix_key(path),
                    "size_bytes": "",
                    "mtime_iso": "",
                    "is_symlink": path.is_symlink(),
                    "symlink_target": "",
                    "inferred_subject_id": "",
                    "inferred_session_id": "",
                    "inferred_task_id": "",
                    "inferred_run_id": "",
                    "file_role": "ERROR",
                    "error": str(exc),
                }
            )
            continue

        rel = str(path.relative_to(root))
        inferred = infer_from_relative_path(rel)
        role = infer_file_role(path, raw_formats)
        suffix = suffix_key(path)
        subset = top_level_subset(rel)
        size = stat.st_size
        target = ""
        if path.is_symlink():
            try:
                target = os.readlink(path)
            except OSError:
                target = ""

        row = {
            "path": str(path),
            "relative_path": rel,
            "top_level_subset": subset,
            "parent_dir": str(Path(rel).parent),
            "suffix": suffix,
            "size_bytes": size,
            "mtime_iso": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "is_symlink": path.is_symlink(),
            "symlink_target": target,
            "inferred_subject_id": inferred["inferred_subject_id"],
            "inferred_session_id": inferred["inferred_session_id"],
            "inferred_task_id": inferred["inferred_task_id"],
            "inferred_run_id": inferred["inferred_run_id"],
            "file_role": role,
            "error": "",
        }
        rows.append(row)
        suffix_counts[suffix] += 1
        suffix_sizes[suffix] += size
        subset_counts[subset] += 1
        role_counts[role] += 1
        total_size += size
        if role == "raw_eeg":
            raw_size += size
            subset_raw_counts[subset] += 1
        if inferred["inferred_subject_id"]:
            subjects.add(inferred["inferred_subject_id"])
        if inferred["inferred_session_id"]:
            sessions.add(inferred["inferred_session_id"])
        if inferred["inferred_task_id"]:
            tasks.add(inferred["inferred_task_id"])

    summary = {
        "dataset_name": args.dataset_name,
        "input_root": str(root),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "follow_symlinks": args.follow_symlinks,
        "max_files": args.max_files,
        "raw_formats": sorted(raw_formats),
        "total_files": len(rows),
        "total_size_bytes": total_size,
        "raw_eeg_files": role_counts["raw_eeg"],
        "raw_eeg_size_bytes": raw_size,
        "metadata_files": role_counts["metadata"],
        "annotation_files": role_counts["annotation"],
        "text_report_files": role_counts["text_report"],
        "file_count_by_suffix": dict(sorted(suffix_counts.items())),
        "size_bytes_by_suffix": dict(sorted(suffix_sizes.items())),
        "file_count_by_top_level_subset": dict(sorted(subset_counts.items())),
        "raw_eeg_count_by_top_level_subset": dict(sorted(subset_raw_counts.items())),
        "file_count_by_role": dict(sorted(role_counts.items())),
        "inferred_subject_count": len(subjects),
        "inferred_session_count": len(sessions),
        "inferred_task_count": len(tasks),
    }
    return rows, summary


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "path",
        "relative_path",
        "top_level_subset",
        "parent_dir",
        "suffix",
        "size_bytes",
        "mtime_iso",
        "is_symlink",
        "symlink_target",
        "inferred_subject_id",
        "inferred_session_id",
        "inferred_task_id",
        "inferred_run_id",
        "file_role",
        "error",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a dataset file inventory.")
    parser.add_argument("--dataset-name", required=True)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--raw-formats", default=",".join(sorted(DEFAULT_RAW_FORMATS)))
    parser.add_argument("--follow-symlinks", action="store_true")
    parser.add_argument("--max-files", type=int, default=0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.input_root.is_dir():
        raise SystemExit(f"[ERROR] input root does not exist: {args.input_root}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = build_inventory(args)
    write_csv(args.output_dir / "dataset_inventory.csv", rows)
    (args.output_dir / "dataset_inventory_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[INFO] Inventory files: {summary['total_files']}")
    print(f"[INFO] Raw EEG files: {summary['raw_eeg_files']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
