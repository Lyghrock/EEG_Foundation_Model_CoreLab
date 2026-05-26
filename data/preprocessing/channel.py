"""通道选择工具: 只保留 EEG 通道。"""

from __future__ import annotations

import mne


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
