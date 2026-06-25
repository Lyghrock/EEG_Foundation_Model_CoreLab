#!/usr/bin/env python3
"""
down_EEG.py — 并行批量下载 OpenNeuro EEG 数据集 + 可选预处理压缩
====================================================================

用法:
  # 查看有哪些 EEG 数据集及大小（不下载）
  python down_EEG.py --dry-run

  # 4 核并行下载, 最多 500GB
  python down_EEG.py --max-size 500 --output-dir /mnt/ddn/weijun/EEG --max-workers 4

  # MB 为单位设置下载上限 (100 GB = 102400 MB)
  python down_EEG.py --max-size-mb 102400 --max-workers 8

  # 8 核并行下载, 下载后自动预处理压缩 + 对齐
  python down_EEG.py --max-size-mb 102400 --max-workers 8 --preprocess \\
      --target-fs 250 --standard-channels "Fp1,Fz,Cz,Pz,O1,O2" --target-duration 300

  # 只下载指定数据集
  python down_EEG.py --dataset ds002778

  # 对已下载的数据集单独执行预处理 (通道对齐 + 长度对齐)
  python down_EEG.py --preprocess-only --output-dir /mnt/ddn/weijun/EEG \\
      --target-fs 250 --standard-channels "Fp1,Fz,Cz,Pz" --target-duration 300

依赖: pip install mne openneuro-py requests
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

GRAPHQL_URL = "https://openneuro.org/crn/graphql"
GRAPHQL_HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://openneuro.org",
    "Referer": "https://openneuro.org/",
    "User-Agent": "EEG-FM-data-prep/0.1",
}
PAGE_SIZE = 100
COMPLETE_MARKER = ".download_complete.json"


# ──────────────────────────────────────────────────────────────────────
# 数据类型
# ──────────────────────────────────────────────────────────────────────

@dataclass
class EegDataset:
    id: str
    name: str
    size_bytes: int = 0

    @property
    def size_gb(self) -> float:
        return self.size_bytes / (1024 ** 3)

    @property
    def size_str(self) -> str:
        if self.size_bytes < 1024 ** 3:
            return f"{self.size_bytes / (1024 ** 2):.1f} MB"
        return f"{self.size_gb:.1f} GB"


# ──────────────────────────────────────────────────────────────────────
# 线程安全下载追踪器
# ──────────────────────────────────────────────────────────────────────

class AtomicTracker:
    """线程安全的下载量追踪, 用于并行模式下控制总空间。"""

    def __init__(self, max_size_gb: Optional[float] = None):
        self._downloaded_gb = 0.0
        self._lock = threading.Lock()
        self.max_size_gb = max_size_gb

    @property
    def downloaded_gb(self) -> float:
        with self._lock:
            return self._downloaded_gb

    def add(self, gb: float):
        with self._lock:
            self._downloaded_gb += gb

    @property
    def remaining(self) -> float:
        with self._lock:
            if self.max_size_gb is None:
                return float("inf")
            return self.max_size_gb - self._downloaded_gb

    def can_download(self, size_gb: float) -> bool:
        """检查下载此数据集后是否会超出空间上限。"""
        if self.max_size_gb is None:
            return True
        with self._lock:
            return self._downloaded_gb + size_gb <= self.max_size_gb


# ──────────────────────────────────────────────────────────────────────
# GraphQL 查询
# ──────────────────────────────────────────────────────────────────────

def query_graphql(
    query: str,
    timeout: int = 30,
    retries: int = 5,
    backoff_sec: float = 2.0,
) -> dict:
    """Run an OpenNeuro GraphQL query with retry.

    OpenNeuro occasionally closes HTTPS connections mid-page. Returning a
    partial dataset list is worse than failing loudly, so callers validate that
    a complete page payload is present before continuing.
    """
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                GRAPHQL_URL,
                json={"query": query},
                headers=GRAPHQL_HEADERS,
                timeout=timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
            errors = payload.get("errors")
            if errors and not payload.get("data"):
                message = errors[0].get("message", errors[0])
                raise RuntimeError(f"GraphQL returned errors: {message}")
            return payload
        except Exception as e:
            last_error = e
            if attempt < retries:
                wait = backoff_sec * attempt
                print(
                    f"[WARN] GraphQL 失败 ({attempt}/{retries}): {e}; "
                    f"{wait:.1f}s 后重试",
                    file=sys.stderr,
                )
                time.sleep(wait)

    print(
        f"[ERROR] GraphQL 失败 ({retries}/{retries}): {last_error}",
        file=sys.stderr,
    )
    return {}


def fetch_all_eeg() -> list[EegDataset]:
    """分页查询 OpenNeuro 上所有 modality=EEG 的数据集。"""
    seen: dict[str, EegDataset] = {}
    cursor: Optional[str] = None

    while True:
        after = f'after: "{cursor}", ' if cursor else ""
        q = f"""
        {{
            datasets(modality: "EEG", first: {PAGE_SIZE}, {after}) {{
                edges {{
                    cursor
                    node {{
                        id
                        name
                        latestSnapshot {{
                            description {{ Name }}
                            summary {{ size }}
                        }}
                    }}
                }}
                pageInfo {{ hasNextPage endCursor }}
            }}
        }}
        """
        result = query_graphql(q)
        datasets_payload = result.get("data", {}).get("datasets")
        if datasets_payload is None:
            cursor_str = cursor or "start"
            raise RuntimeError(
                "OpenNeuro 数据集分页查询未完成: "
                f"cursor={cursor_str}, 已收集={len(seen)}"
            )

        edges = datasets_payload["edges"]
        page_info = datasets_payload["pageInfo"]

        for e in edges:
            n = e["node"]
            ds_id = n["id"]
            if ds_id in seen:
                continue
            snap = n.get("latestSnapshot") or {}
            summary = snap.get("summary") or {}
            desc = snap.get("description") or {}
            size_raw = summary.get("size")
            try:
                size = int(size_raw) if size_raw else 0
            except (ValueError, TypeError):
                size = 0
            name = n.get("name", "") or desc.get("Name", "") or ds_id
            seen[ds_id] = EegDataset(
                id=ds_id, name=name.strip(), size_bytes=size
            )

        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]

    return list(seen.values())


# ──────────────────────────────────────────────────────────────────────
# 单数据集下载
# ──────────────────────────────────────────────────────────────────────

def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _complete_marker(ds_path: Path) -> Path:
    return ds_path / COMPLETE_MARKER


def _has_complete_marker(ds_path: Path) -> bool:
    return _complete_marker(ds_path).is_file()


def _write_complete_marker(ds_path: Path, ds_id: str, backend: str, size_bytes: int):
    marker = {
        "dataset": ds_id,
        "backend": backend,
        "size_bytes": size_bytes,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    _complete_marker(ds_path).write_text(
        json.dumps(marker, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _aws_command() -> Optional[list[str]]:
    aws_bin = shutil.which("aws")
    if aws_bin:
        return [aws_bin]

    try:
        subprocess.run(
            [sys.executable, "-m", "awscli", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return [sys.executable, "-m", "awscli"]
    except Exception:
        return None


def _resolve_backend(download_backend: str) -> str:
    if download_backend == "auto":
        return "aws" if _aws_command() else "openneuro"
    return download_backend


def _download_command(ds_id: str, ds_path: Path, backend: str) -> list[str]:
    if backend == "aws":
        aws_cmd = _aws_command()
        if not aws_cmd:
            raise RuntimeError(
                "AWS backend requested but awscli is not available. "
                "Install requirements.txt or use --download-backend openneuro."
            )
        return [
            *aws_cmd,
            "s3",
            "sync",
            "--no-sign-request",
            "--only-show-errors",
            f"s3://openneuro.org/{ds_id}",
            str(ds_path),
        ]

    if backend == "openneuro":
        return [
            sys.executable,
            "-m",
            "openneuro",
            "download",
            "--dataset",
            ds_id,
            "--target-dir",
            str(ds_path),
        ]

    raise ValueError(f"Unsupported OpenNeuro backend: {backend}")


def download_one(
    ds_id: str,
    target_dir: Path,
    tracker: AtomicTracker,
    quiet: bool,
    download_backend: str,
) -> bool:
    """下载单个数据集, 返回是否成功。"""
    ds_path = target_dir / ds_id

    # Only skip datasets that this script has marked complete. A leftover
    # directory without the marker is treated as an interrupted download and
    # the backend is run again so it can fill missing files.
    if ds_path.exists() and _has_complete_marker(ds_path):
        exist = _dir_size(ds_path)
        tracker.add(exist / (1024 ** 3))
        if not quiet:
            print(f"  [SKIP] {ds_id}: 已完成 ({exist/1e9:.1f} GB)")
        return True

    if not quiet:
        action = "RESUME" if ds_path.exists() else "DOWNLOAD"
        print(f"\n  [{action}] {ds_id} ...")

    try:
        ds_path.mkdir(parents=True, exist_ok=True)
        backend = _resolve_backend(download_backend)
        if not quiet:
            print(f"  [BACKEND] {backend}")
        start = time.time()
        proc = subprocess.Popen(
            _download_command(ds_id, ds_path, backend),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        for line in proc.stdout:
            if not quiet:
                sys.stdout.write(line)
                sys.stdout.flush()
        proc.wait()

        if proc.returncode != 0:
            print(f"  [ERROR] {ds_id}: return code {proc.returncode}")
            return False

        # 兼容 openneuro 在目标目录下再创建一层 ds_id 的情况。
        nested = ds_path / ds_id
        if nested.exists() and nested.is_dir():
            for child in nested.iterdir():
                shutil.move(str(child), str(ds_path / child.name))
            os.rmdir(nested)

        actual = _dir_size(ds_path)
        actual_gb = actual / (1024 ** 3)
        _write_complete_marker(ds_path, ds_id, backend, actual)
        tracker.add(actual_gb)
        elapsed = time.time() - start
        speed = actual_gb / elapsed * 60 if elapsed > 0 else 0
        print(f"  [DONE] {actual_gb:.1f} GB ({speed:.2f} GB/min)")
        return True

    except FileNotFoundError:
        print("\n  [ERROR] 下载后端不可用: 请安装 openneuro-py 或 awscli")
        return False
    except Exception as e:
        print(f"\n  [ERROR] {ds_id}: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────
# 并行下载 + 预处理
# ──────────────────────────────────────────────────────────────────────

def run_pipeline(
    datasets: list[EegDataset],
    output_dir: Path,
    max_size: Optional[float],
    max_workers: int,
    download_backend: str,
    preprocess: bool,
    preprocess_kwargs: Optional[dict],
    quiet: bool,
):
    """并行下载数据集 (可选预处理), 实时打印进度。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    tracker = AtomicTracker(max_size_gb=max_size)

    total = len(datasets)
    completed = 0
    failed: list[str] = []
    skipped: list[str] = []
    stop_event = threading.Event()

    def worker(ds: EegDataset) -> tuple[str, str]:
        if stop_event.is_set():
            return ds.id, "stopped"

        if not tracker.can_download(ds.size_gb):
            return ds.id, "limit_skip"

        # ← 下载
        ok = download_one(
            ds.id,
            output_dir,
            tracker,
            quiet=quiet,
            download_backend=download_backend,
        )

        # ← 预处理 (可选)
        if ok and preprocess:
            _maybe_preprocess(output_dir / ds.id, preprocess_kwargs, quiet)

        if ok:
            return ds.id, "ok"
        else:
            return ds.id, "fail"

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futs = {pool.submit(worker, ds): ds for ds in datasets}

        for future in as_completed(futs):
            ds = futs[future]
            ds_id, status = future.result()
            completed += 1

            if status == "ok":
                pass
            elif status == "fail":
                failed.append(ds_id)
            elif status == "limit_skip":
                skipped.append(ds_id)
                stop_event.set()
            elif status == "stopped":
                skipped.append(ds_id)

            # 进度条
            _print_progress(
                completed, total, tracker.downloaded_gb, max_size, failed
            )

    # 最终报告
    print(f"\n{'=' * 60}")
    print(f"  完成: {completed - len(failed) - len(skipped)} / {total} 个数据集")
    print(f"  累计下载: {tracker.downloaded_gb:.1f} GB")
    if failed:
        print(f"  失败: {', '.join(failed)}")
    if skipped:
        print(f"  跳过: {len(skipped)} 个 (超出空间上限)")
    print(f"  输出目录: {output_dir.resolve()}")
    print(f"{'=' * 60}\n")


def _maybe_preprocess(ds_path: Path, kwargs: Optional[dict], quiet: bool):
    """安全执行预处理, 依赖不存在时优雅降级。"""
    try:
        from preprocessing import preprocess_dataset

        preprocess_dataset(str(ds_path), quiet=quiet, **(kwargs or {}))
    except ImportError:
        print("  [WARN] preprocessing 模块不可用, 跳过预处理")
    except Exception as e:
        print(f"  [WARN] 预处理失败: {e}")


def _print_progress(
    completed: int,
    total: int,
    downloaded_gb: float,
    max_size: Optional[float],
    failed: list[str],
):
    pct = downloaded_gb / max_size * 100 if max_size else 0
    limit_str = f" / {max_size:.0f} GB ({pct:.0f}%)" if max_size else ""
    fail_str = f", {len(failed)} 失败" if failed else ""
    print(
        f"  ── 进度: {completed}/{total} 个"
        f" | 已用 {downloaded_gb:.1f} GB{limit_str}{fail_str}"
    )


# ──────────────────────────────────────────────────────────────────────
# 后处理: 对已下载的数据执行预处理
# ──────────────────────────────────────────────────────────────────────

def preprocess_existing(output_dir: Path, kwargs: Optional[dict], quiet: bool):
    """扫描已下载目录, 对所有数据集执行预处理。"""
    from preprocessing import preprocess_dataset

    ds_dirs = sorted(
        d for d in output_dir.iterdir()
        if d.is_dir() and d.name.startswith("ds")
    )
    if not ds_dirs:
        print(f"未在 {output_dir} 下找到 BIDS 数据集目录")
        return

    print(f"对 {len(ds_dirs)} 个数据集执行预处理...\n")
    total_saved = 0.0
    for d in ds_dirs:
        before = _dir_size(d)
        preprocess_dataset(str(d), quiet=quiet, **(kwargs or {}))
        after = _dir_size(d)
        saved = before - after
        total_saved += saved
        print(f"  {d.name}: {before/1e9:.1f} → {after/1e9:.1f} GB"
              f" (节省 {saved/1e9:.1f} GB)\n")

    print(f"{'=' * 50}")
    print(f"总计节省: {total_saved/1e9:.1f} GB")


# ──────────────────────────────────────────────────────────────────────
# 打印
# ──────────────────────────────────────────────────────────────────────

def print_table(datasets: list[EegDataset]):
    total = sum(d.size_bytes for d in datasets) / (1024 ** 3)
    print(f"\n{'=' * 90}")
    print(f"  EEG 数据集共 {len(datasets)} 个, 总大小约 {total:.0f} GB")
    print(f"{'=' * 90}")
    print(f"  {'ID':<12} {'大小':<12} {'名称'}")
    print(f"  {'-' * 70}")
    for d in datasets:
        print(f"  {d.id:<12} {d.size_str:<12} {d.name[:55]}")
    print(f"{'=' * 90}\n")


# ──────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="OpenNeuro EEG 批量下载 (支持并行 + 预处理压缩)"
    )
    # 下载控制
    p.add_argument("--max-size", type=float, default=None, metavar="GB",
                   help="空间上限 (GB), 超出后停止")
    p.add_argument("--max-size-mb", type=float, default=None, metavar="MB",
                   help="空间上限 (MB), 优先于 --max-size 使用")
    p.add_argument("--output-dir", type=Path, default=Path("./openneuro_eeg"),
                   help="下载目录 (默认: ./openneuro_eeg)")
    p.add_argument("--dataset", type=str, default=None,
                   help="只下载指定 dataset (e.g. ds002778)")
    p.add_argument("--datasets-file", type=Path, default=None,
                   help="从文件补充 dataset ID (每行一个)")

    # 排序 / 预览
    p.add_argument("--dry-run", "--dryrun", action="store_true", dest="dry_run",
                   help="仅预览, 不下载")
    p.add_argument("--sort", type=str, default=None,
                   choices=["size", "name"],
                   help="排序: size / name")

    # 并行
    p.add_argument("--max-workers", type=int, default=4, metavar="N",
                   help="并行下载数 (默认 4, 受 CPU 核心数 & 带宽限制)")
    p.add_argument("--download-backend", type=str, default="auto",
                   choices=["auto", "openneuro", "aws"],
                   help="下载后端: auto/aws/openneuro (默认 auto, 有 awscli 时走公开 S3)")

    # 预处理
    p.add_argument("--preprocess", action="store_true",
                   help="下载后自动执行预处理压缩")
    p.add_argument("--preprocess-only", action="store_true",
                   help="仅对已下载的数据执行预处理, 不下载")
    p.add_argument("--target-fs", type=int, default=250, metavar="HZ",
                   help="预处理目标采样率 (默认 250 Hz, 0=不降采样)")
    p.add_argument("--no-align-sfreq", action="store_true",
                   help="不强制对齐采样率 (默认强制所有文件到 --target-fs, 含升采样)")
    p.add_argument("--standard-channels", type=str, default=None, metavar="CHS",
                   help="对齐到标准通道集 (逗号分隔, 默认不启用)")
    p.add_argument("--target-duration", type=float, default=None, metavar="SEC",
                   help="对齐到目标时长秒数 (默认不启用)")
    p.add_argument("--length-mode", type=str, default="crop",
                   choices=["crop", "truncate", "pad"],
                   help="长度对齐模式 (默认 crop, 长截短补)")
    p.add_argument("--interpolate-channels", action="store_true",
                   help="插值缺失的标准通道 (需先设置 montage)")
    p.add_argument("--no-remove-original", action="store_true",
                   help="预处理后保留原始文件 (默认删除)")

    # 杂项
    p.add_argument("--quiet", action="store_true", help="安静模式")
    return p


def main():
    args = build_parser().parse_args()

    # ── 空间上限: MB 优先于 GB, 0 表示不限 ──────────────────────────
    max_size = args.max_size_mb / 1024 if args.max_size_mb is not None else args.max_size
    if max_size is not None and max_size <= 0:
        max_size = None

    # ── 预处理参数 ──────────────────────────────────────────────────
    preprocess_kwargs = {
        "target_fs": args.target_fs,
        "align_sfreq": not args.no_align_sfreq,
        "remove_original": not args.no_remove_original,
    }
    if args.standard_channels is not None:
        preprocess_kwargs["standard_channels"] = [
            ch.strip() for ch in args.standard_channels.split(",") if ch.strip()
        ]
        preprocess_kwargs["interpolate_missing"] = args.interpolate_channels
    if args.target_duration is not None:
        preprocess_kwargs["target_duration_sec"] = args.target_duration
        preprocess_kwargs["length_mode"] = args.length_mode

    # ── 仅预处理 ────────────────────────────────────────────────────
    if args.preprocess_only:
        preprocess_existing(
            args.output_dir, preprocess_kwargs, quiet=args.quiet
        )
        return

    # ── 查询 ──────────────────────────────────────────────────────
    print("[1/4] 正在查询 OpenNeuro (modality=EEG) ...")
    try:
        datasets = fetch_all_eeg()
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  -> 找到 {len(datasets)} 个 EEG 数据集\n")
    if not datasets:
        sys.exit(1)

    # 补充
    if args.datasets_file and args.datasets_file.exists():
        ids = {d.id for d in datasets}
        with open(args.datasets_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and line not in ids:
                    datasets.append(
                        EegDataset(id=line, name=f"[manual] {line}")
                    )
                    ids.add(line)

    # 过滤
    if args.dataset:
        datasets = [d for d in datasets if d.id == args.dataset]
        if not datasets:
            print(f"[ERROR] 未找到 {args.dataset}")
            sys.exit(1)

    # 排序
    sort_map = {"size": "size_bytes", "name": "name"}
    if args.sort and args.sort in sort_map:
        datasets.sort(
            key=lambda d: getattr(d, sort_map[args.sort]),
            reverse=(args.sort == "size"),
        )

    # ── 打印 ──────────────────────────────────────────────────────
    print_table(datasets)
    total_est = sum(d.size_bytes for d in datasets) / (1024 ** 3)
    if max_size:
        print(f"  空间上限: {max_size:.1f} GB"
              f" ({max_size * 1024:.0f} MB)"
              f" | 全部估计: {total_est:.0f} GB")
    if args.max_workers > 1:
        print(f"  并行下载: {args.max_workers} 个 worker")
    print(f"  下载后端: {args.download_backend}"
          f" -> {_resolve_backend(args.download_backend)}")
    if args.preprocess:
        print(f"  预处理  : 已启用")
        print(f"    采样率: {args.target_fs} Hz"
              f" ({'强制对齐' if not args.no_align_sfreq else '仅降采样'})")
        if args.standard_channels:
            print(f"    通道集: {', '.join(preprocess_kwargs['standard_channels'][:8])}"
                  f"{'...' if len(preprocess_kwargs['standard_channels']) > 8 else ''}")
        if args.target_duration:
            print(f"    时长  : {args.target_duration}s ({args.length_mode})")
        print(f"    原始文件: {'删除' if not args.no_remove_original else '保留'}")

    if args.dry_run:
        print("\n[DRY-RUN] 未下载\n")
        return

    # ── 执行 ──────────────────────────────────────────────────────
    print(f"\n[2/4] 下载目录: {args.output_dir.resolve()}")
    print(f"[3/4] 开始下载...\n")

    run_pipeline(
        datasets=datasets,
        output_dir=args.output_dir,
        max_size=max_size,
        max_workers=args.max_workers,
        download_backend=args.download_backend,
        preprocess=args.preprocess,
        preprocess_kwargs=preprocess_kwargs if args.preprocess else None,
        quiet=args.quiet,
    )

    print("[4/4] 完成")


if __name__ == "__main__":
    main()
