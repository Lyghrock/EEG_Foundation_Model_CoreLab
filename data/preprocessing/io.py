"""EEG 文件 I/O 工具: 格式检测、读写。"""

from __future__ import annotations

import shutil
from pathlib import Path

import mne

# 常见的 EEG 数据文件扩展名 (BIDS 中可能出现)
EEG_EXTENSIONS = {
    ".set",    # EEGLAB
    ".edf",    # European Data Format
    ".bdf",    # Biosemi
    ".vhdr",   # BrainVision (header)
    ".eeg",    # BrainVision (data)
    ".fif",    # MNE / Neuromag
    ".cnt",    # Neuroscan
    ".egi",    # EGI (simple binary)
    ".mff",    # EGI (MFF)
}

# 只通过主文件读取, 跳过附属数据文件
# (.fdt 由 .set 加载, .vmrk 由 .vhdr 加载)
SKIP_EXTENSIONS = {".fdt", ".vmrk"}


def find_eeg_files(ds_path: Path) -> list[Path]:
    """递归扫描 BIDS 数据集目录, 返回所有 EEG 数据文件路径。

    跳过 ``derivatives/`` 目录下的文件, 避免重复处理。
    跳过附属文件 (.fdt, .vmrk), 它们会被主文件读取。
    """
    ds_path = Path(ds_path)
    files: list[Path] = []
    for ext in sorted(EEG_EXTENSIONS):
        files.extend(ds_path.rglob(f"*{ext}"))

    # 跳过 derivatives 和附属文件
    result: list[Path] = []
    for f in files:
        rel = f.relative_to(ds_path)
        if any(p == "derivatives" for p in rel.parts):
            continue
        if f.suffix.lower() in SKIP_EXTENSIONS:
            continue
        result.append(f)
    return result


def read_eeg(file_path: Path, preload: bool = True) -> mne.io.Raw | None:
    """自动检测格式并读取 EEG 文件。

    返回值:
        - ``mne.io.Raw`` 对象 (成功)
        - ``None`` (无法识别或读取失败)
    """
    path_str = str(file_path)
    suffix = file_path.suffix.lower()

    readers = {
        ".fif": lambda: mne.io.read_raw_fif(path_str, preload=preload),
        ".set": lambda: mne.io.read_raw_eeglab(path_str, preload=preload),
        ".vhdr": lambda: mne.io.read_raw_brainvision(path_str, preload=preload),
        ".edf": lambda: mne.io.read_raw_edf(path_str, preload=preload),
        ".bdf": lambda: mne.io.read_raw_bdf(path_str, preload=preload),
        ".cnt": lambda: mne.io.read_raw_cnt(path_str, preload=preload),
        ".egi": lambda: mne.io.read_raw_egi(path_str, preload=preload),
        ".mff": lambda: mne.io.read_raw_egi(path_str, preload=preload),
        ".eeg": lambda: _read_brainvision_data(path_str, file_path, preload),
    }

    reader = readers.get(suffix)
    if reader is None:
        return None

    try:
        return reader()
    except Exception as e:
        print(f"    [WARN] 读取失败 {file_path.name}: {e}")
        return None


def _read_brainvision_data(path_str, file_path, preload):
    """.eeg 文件可能是 BrainVision 数据文件, 查找配套 .vhdr 再读取。"""
    vhdr = file_path.with_suffix(".vhdr")
    if vhdr.exists():
        return mne.io.read_raw_brainvision(str(vhdr), preload=preload)
    # 也可能是其他格式, 尝试通用读取
    return mne.io.read_raw_edf(path_str, preload=preload)


def save_as_compressed(raw: mne.io.Raw, output_path: Path) -> Path:
    """将 Raw 对象保存为压缩 FIF, 返回输出路径。"""
    output_path = output_path.with_suffix(".fif.gz")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw.save(str(output_path), overwrite=True, verbose=False)
    return output_path


def remove_originals(eeg_files: list[Path], ds_path: Path):
    """删除原始 EEG 文件 (及其附属文件)。

    同时清理可能存在的空目录。
    """
    # 收集需要删除的所有文件 (包括附属文件)
    to_remove = set(eeg_files)
    for f in eeg_files:
        # 如果主文件是 .set, 删除对应的 .fdt
        if f.suffix.lower() == ".set":
            fdt = f.with_suffix(".fdt")
            if fdt.exists():
                to_remove.add(fdt)
        # 如果主文件是 .vhdr, 删除对应的 .vmrk 和 .eeg
        if f.suffix.lower() == ".vhdr":
            for ext in (".vmrk", ".eeg"):
                p = f.with_suffix(ext)
                if p.exists():
                    to_remove.add(p)

    for f in sorted(to_remove):
        try:
            f.unlink()
        except OSError as e:
            print(f"    [WARN] 删除失败 {f.name}: {e}")

    # 清理空目录
    _remove_empty_dirs(ds_path)


def _remove_empty_dirs(root: Path):
    """自底向上删除空目录。"""
    for d in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
