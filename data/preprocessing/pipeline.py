"""预处理管线: 对已下载的 BIDS EEG 数据集执行压缩预处理。

流程:
    1. 扫描数据集目录, 找到所有 EEG 文件
    2. 逐个读取 → 只保留 EEG 通道 → 对齐通道 → 对齐采样率 → 对齐长度 → 保存为压缩 FIF
    3. 成功后删除原始文件

用法 (给下游脚本调用的接口)::

    from preprocessing import preprocess_dataset
    preprocess_dataset("/path/to/ds002778", target_fs=250)
"""

from __future__ import annotations

import typing as t
from pathlib import Path

from .channel import align_channels, select_eeg_channels
from .io import find_eeg_files, read_eeg, save_as_compressed, remove_originals
from .length import align_length


def preprocess_dataset(
    ds_path: str | Path,
    *,
    target_fs: int = 250,
    align_sfreq: bool = True,
    standard_channels: t.Optional[list[str]] = None,
    interpolate_missing: bool = False,
    target_duration_sec: t.Optional[float] = None,
    length_mode: t.Literal["crop", "truncate", "pad"] = "crop",
    remove_original: bool = True,
    quiet: bool = False,
) -> int:
    """对单个 BIDS EEG 数据集执行压缩预处理。

    参数:
        ds_path: 数据集根目录。
        target_fs: 目标采样率 (Hz)。设为 ``0`` 跳过降采样。
        align_sfreq: 是否强制对齐所有文件到 ``target_fs`` (包括升采样)。
                     默认为 ``True``; 若为 ``False`` 则只在原始采样率高于目标时降采样。
        standard_channels: 标准通道集列表。提供后会对齐到该通道集 (保留交集并按序重排)。
        interpolate_missing: 是否插值缺失的标准通道 (需先设置 montage)。
        target_duration_sec: 目标时长 (秒)。提供后会对齐到该时长。
        length_mode: 长度对齐模式, 见 ``align_length()``。
        remove_original: 预处理成功后是否删除原始 EEG 文件。
        quiet: 安静模式。

    返回:
        成功处理的文件数。
    """
    ds_path = Path(ds_path)
    eeg_files = find_eeg_files(ds_path)

    if not eeg_files:
        if not quiet:
            print(f"  [PREPROC] {ds_path.name}: 未找到 EEG 文件, 跳过")
        return 0

    if not quiet:
        print(f"  [PREPROC] {ds_path.name}: 发现 {len(eeg_files)} 个 EEG 文件")

    # 预处理输出目录: derivatives/preprocessed/<subject>/eeg/
    deriv_root = ds_path / "derivatives" / "preprocessed"
    processed = 0
    failed = 0

    for src in sorted(eeg_files):
        # 确定输出路径, 保持 BIDS 子目录结构
        rel = src.relative_to(ds_path)
        dst = deriv_root / rel

        if dst.with_suffix(".fif.gz").exists():
            if not quiet:
                print(f"    [SKIP] {rel}: 已预处理")
            processed += 1
            continue

        if not quiet:
            print(f"    [READ]  {rel}")

        raw = read_eeg(src, preload=True)
        if raw is None:
            failed += 1
            continue

        try:
            # 步骤 1: 选择 EEG 通道
            raw = select_eeg_channels(raw)

            # 步骤 2: 对齐到标准通道集 (若指定)
            if standard_channels is not None:
                raw = align_channels(
                    raw,
                    standard_channels=standard_channels,
                    interpolate_missing=interpolate_missing,
                )

            # 步骤 3: 采样率对齐
            if target_fs > 0:
                orig_fs = raw.info["sfreq"]
                if align_sfreq:
                    # 强制对齐: 不等于目标值就重采样 (包括升采样)
                    if abs(orig_fs - target_fs) > 1:
                        if not quiet:
                            direction = "升采样" if target_fs > orig_fs else "降采样"
                            print(f"    [INFO] {direction}: {orig_fs:.0f} → {target_fs} Hz")
                        raw.resample(target_fs, verbose=False)
                else:
                    # 仅当原始采样率高于目标时才降采样
                    if target_fs < orig_fs:
                        if not quiet:
                            print(f"    [INFO] 降采样: {orig_fs:.0f} → {target_fs} Hz")
                        raw.resample(target_fs, verbose=False)

            # 步骤 4: 长度对齐 (若指定)
            if target_duration_sec is not None and target_duration_sec > 0:
                raw = align_length(
                    raw,
                    target_duration_sec=target_duration_sec,
                    mode=length_mode,
                )

            # 步骤 5: 保存为压缩 FIF
            saved = save_as_compressed(raw, dst)
            if not quiet:
                orig_size = src.stat().st_size if src.exists() else 0
                new_size = saved.stat().st_size
                ratio = (1 - new_size / orig_size) * 100 if orig_size > 0 else 0
                print(f"    [SAVE] {rel.parent.name}/{saved.name}  "
                      f"({new_size/1024**2:.1f} MB, 节省 {ratio:.0f}%)")
            processed += 1

        except Exception as e:
            print(f"    [ERROR] {rel}: {e}")
            failed += 1
        finally:
            raw.close()

    # 删除原始文件
    if remove_original and processed > 0 and failed == 0:
        remove_originals(eeg_files, ds_path)
        if not quiet:
            print(f"  [PREPROC] 已删除原始 EEG 文件 ({len(eeg_files)} 个)")

    if not quiet:
        print(f"  [PREPROC] 完成: {processed} 成功, {failed} 失败")
    return processed
