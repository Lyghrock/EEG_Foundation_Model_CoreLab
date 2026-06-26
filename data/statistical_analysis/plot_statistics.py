#!/usr/bin/env python3
"""Render plots from precomputed plot_data artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def render_bar(rows: list[dict[str, str]], spec: dict[str, Any], out_path: Path) -> None:
    x_key = spec.get("x", "value")
    y_key = spec.get("y", "count")
    labels = [row.get(x_key, "") for row in rows]
    values = [to_float(row.get(y_key)) for row in rows]
    width = max(8, min(24, 0.35 * max(1, len(labels))))
    fig, ax = plt.subplots(figsize=(width, 5))
    ax.bar(range(len(labels)), values, color="#3b6ea8")
    ax.set_title(spec.get("title", spec.get("name", "")))
    ax.set_xlabel(f"{x_key} ({spec.get('unit', '')})".strip())
    ax.set_ylabel(y_key)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def render_histogram(rows: list[dict[str, str]], spec: dict[str, Any], out_path: Path) -> None:
    x_key = spec.get("x", "bin_center")
    y_key = spec.get("y", "count")
    left_key = spec.get("x_left")
    right_key = spec.get("x_right")
    centers = [to_float(row.get(x_key)) for row in rows]
    values = [to_float(row.get(y_key)) for row in rows]
    if left_key and right_key:
        lefts = [to_float(row.get(left_key)) for row in rows]
        rights = [to_float(row.get(right_key)) for row in rows]
        widths = [max(0.0, r - l) for l, r in zip(lefts, rights)]
    else:
        widths = [0.8 for _ in centers]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.bar(centers, values, width=widths, align="center", color="#4a8f6a", edgecolor="white", linewidth=0.3)
    ax.set_title(spec.get("title", spec.get("name", "")))
    ax.set_xlabel(spec.get("unit", "value"))
    ax.set_ylabel(y_key)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)


def render_plot(plot_data_dir: Path, plots_dir: Path, spec: dict[str, Any]) -> None:
    source = plot_data_dir / spec["source_csv"]
    if not source.exists():
        print(f"[WARN] plot source missing: {source}")
        return
    rows = read_csv_rows(source)
    if not rows:
        print(f"[WARN] plot source is empty: {source}")
        return
    out_path = plots_dir / spec.get("output_png", f"{spec['name']}.png")
    plot_type = spec.get("plot_type")
    if plot_type == "bar":
        render_bar(rows, spec, out_path)
    elif plot_type == "histogram":
        render_histogram(rows, spec, out_path)
    else:
        print(f"[WARN] unsupported plot type: {plot_type}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render plots from EEG plot_data artifacts.")
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    plot_data_dir = args.output_dir / "plot_data"
    manifest_path = plot_data_dir / "plot_manifest.json"
    if not manifest_path.exists():
        raise SystemExit(f"[ERROR] missing plot manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    plots_dir = args.output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    for spec in manifest.get("plots", []):
        render_plot(plot_data_dir, plots_dir, spec)
    print(f"[INFO] Plots written to: {plots_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
