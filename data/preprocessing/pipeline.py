"""预处理管线: 对已下载的 BIDS EEG 数据集执行压缩预处理。

流程:
    1. 扫描数据集目录, 找到所有 EEG 文件
    2. 逐个读取 → 只保留 EEG 通道 → 降采样 → 保存为压缩 FIF
    3. 成功后删除原始文件

用法 (给下游脚本调用的接口)::

    from preprocessing import preprocess_dataset
    preprocess_dataset("/path/to/ds002778", target_fs=250)
"""

from __future__ import annotations

from pathlib import Path

from .channel import select_eeg_channels
from .io import find_eeg_files, read_eeg, save_as_compressed, remove_originals


def preprocess_dataset(
    ds_path: str | Path,
    *,
    target_fs: int = 250,
    remove_original: bool = True,
    quiet: bool = False,
) -> int:
    """对单个 BIDS EEG 数据集执行压缩预处理。

    参数:
        ds_path: 数据集根目录。
        target_fs: 目标采样率 (Hz)。设为 ``None`` 或 ``0`` 跳过降采样。
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

            # 步骤 2: 降采样
            if target_fs and 0 < target_fs < raw.info["sfreq"]:
                if not quiet:
                    print(f"    [INFO] 降采样: {raw.info['sfreq']:.0f} → {target_fs} Hz")
                raw.resample(target_fs, verbose=False)

            # 步骤 3: 保存为压缩 FIF
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
