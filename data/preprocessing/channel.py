"""通道选择工具: 只保留 EEG 通道 + 对齐到标准通道集。"""

from __future__ import annotations

import typing as t

import mne

# 标准 10-20 系统通道 (21 通道常见配置)
STANDARD_EEG_1020 = [
    "Fp1", "Fp2", "F3", "F4", "C3", "C4", "P3", "P4", "O1", "O2",
    "F7", "F8", "T7", "T8", "P7", "P8", "Fz", "Cz", "Pz", "Oz", "FCz",
]

# 异名映射 (旧命名 → 标准命名)
CHANNEL_NAME_MAP = {
    "T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8",
}


def select_eeg_channels(raw: mne.io.Raw) -> mne.io.Raw:
    """只保留 type 为 ``eeg`` 的通道, 剔除 EOG/ECG/EMG/STIM 等。

    如果没有任何通道被标记为 EEG 类型, 尝试用关键词启发式保留典型 EEG 通道。
    """
    ch_types = raw.get_ch_types()

    # 方法一: 按 MNE 类型标记筛选
    eeg_indices = [
        i for i, t in enumerate(ch_types) if t == "eeg"
    ]

    # 方法二: 回退 —— 按名称关键词启发式选择
    if not eeg_indices:
        eeg_indices = _heuristic_eeg_channels(raw.ch_names)

    if not eeg_indices:
        print("    [WARN] 无法识别 EEG 通道, 保留全部通道")
        return raw

    n_before = raw.info["nchan"]
    raw.pick(eeg_indices)
    n_after = raw.info["nchan"]

    dropped = n_before - n_after
    if dropped > 0:
        print(f"    [INFO] 剔除 {dropped} 个非 EEG 通道")
    return raw


def align_channels(
    raw: mne.io.Raw,
    standard_channels: t.Optional[list[str]] = None,
    interpolate_missing: bool = False,
) -> mne.io.Raw:
    """对齐 EEG 通道到标准通道集。

    流程:
        1. 将异名通道映射为标准名 (如 T3 → T7)
        2. 保留原始数据中属于标准通道集的通道
        3. 按标准通道顺序重排
        4. 若 ``interpolate_missing=True``, 用 MNE 插值缺失的标准通道

    参数:
        raw: 输入的 Raw 对象 (已通过 select_eeg_channels 过滤)。
        standard_channels: 标准通道列表, 默认使用 ``STANDARD_EEG_1020``。
        interpolate_missing: 是否插值缺失的标准通道 (需已设置 montage)。

    返回:
        通道对齐后的 Raw 对象。
    """
    if standard_channels is None:
        standard_channels = list(STANDARD_EEG_1020)

    n_before = raw.info["nchan"]

    # 1. 通道重命名
    renames = {}
    for ch in raw.ch_names:
        mapped = CHANNEL_NAME_MAP.get(ch)
        if mapped is not None:
            renames[ch] = mapped
    if renames:
        raw.rename_channels(renames)

    # 2. 找到交集 (保留大小写敏感)
    available = []
    missing = []
    raw_ch_set = set(raw.ch_names)
    for ch in standard_channels:
        if ch in raw_ch_set:
            available.append(ch)
        else:
            # 尝试不区分大小写匹配
            match = [c for c in raw_ch_set if c.upper() == ch.upper()]
            if match:
                available.append(match[0])
            else:
                missing.append(ch)

    if not available:
        print("    [WARN] 未匹配到任何标准通道, 保留全部 EEG 通道")
        return raw

    # 3. 保留 + 重排
    raw.pick(available)

    # 4. 插值缺失通道
    if interpolate_missing and missing:
        try:
            raw.info["bads"] = missing
            raw.interpolate_bads(reset_bads=True, verbose=False)
            print(f"    [INFO] 插值 {len(missing)} 个缺失通道: {', '.join(missing)}")
        except Exception as e:
            print(f"    [WARN] 插值失败 (需先设置 montage): {e}")

    n_after = raw.info["nchan"]
    print(f"    [INFO] 通道对齐: {n_before} → {n_after} 个标准通道"
          f" (缺失 {len(missing)}: {', '.join(missing[:5])}{'...' if len(missing) > 5 else ''})")
    return raw


def _heuristic_eeg_channels(ch_names: list[str]) -> list[int]:
    """基于名称关键词的启发式通道选择。

    保留含 ``eeg``、``EEG``、``C``、``Fp``、``F``、``P``、``O``、``T``
    或数字 (如 ``Fz``、``C3``、``POz``) 的通道, 剔除 ``EOG``、``ECG``、
    ``EMG``、``STI`` 等。
    """
    eeg_keywords = {"eeg", "fz", "cz", "pz", "oz", "fcz", "cpz", "poz"}
    non_eeg = {"eog", "ecg", "emg", "stim", "trigger", "status",
               "accel", "gyro", "magnet", "temperature", "audio"}
    # 常见 EEG 通道前缀: Fp, AF, F, FC, C, CP, P, PO, O, T, FT, TP
    eeg_prefixes = {"fp", "af", "f", "fc", "c", "cp", "p", "po", "o",
                    "t", "ft", "tp", "fz", "cz", "pz", "oz",
                    "fp1", "fp2", "f3", "f4", "f7", "f8",
                    "c3", "c4", "p3", "p4", "o1", "o2",
                    "t3", "t4", "t5", "t6",
                    "fc1", "fc2", "cp1", "cp2",
                    "po3", "po4", "poz", "fcz",
                    "eeg"}

    indices = []
    for i, name in enumerate(ch_names):
        lower = name.lower().strip()

        # 排除已知非 EEG
        if any(key in lower for key in non_eeg):
            continue

        # 检查是否是 EEG 通道名
        lower_stripped = lower.lstrip("0123456789-")
        if any(lower_stripped.startswith(p) for p in eeg_prefixes):
            indices.append(i)
        elif any(key in lower for key in eeg_keywords):
            indices.append(i)
        elif lower.replace("-", "").isalnum() and any(c.isdigit() for c in lower):
            indices.append(i)

    return indices
