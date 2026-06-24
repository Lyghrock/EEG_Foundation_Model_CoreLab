"""EEG 长度对齐工具: 将记录截断或填充到固定时长。"""

from __future__ import annotations

import typing as t

import mne
import numpy as np


def align_length(
    raw: mne.io.Raw,
    target_duration_sec: float,
    mode: t.Literal["truncate", "pad", "crop"] = "crop",
) -> mne.io.Raw:
    """对齐 EEG 记录到目标时长。

    参数:
        raw: 输入的 Raw 对象。
        target_duration_sec: 目标时长 (秒)。
        mode:
            - ``"crop"`` (默认): 长于目标的截断, 短于目标的末尾补零。
            - ``"truncate"``: 长于目标的截断, 短于目标的保留原长。
            - ``"pad"``: 短于目标的末尾补零, 长于目标的保留原长。

    返回:
        时长对齐后的 Raw 对象。
    """
    sfreq = raw.info["sfreq"]
    current_n = raw.n_times
    target_n = int(round(target_duration_sec * sfreq))
    current_dur = current_n / sfreq

    if abs(current_n - target_n) < 2:
        return raw  # 已在目标长度

    # ── 截断 ──────────────────────────────────────────────────────────
    if current_n > target_n and mode in ("truncate", "crop"):
        raw.crop(tmin=0, tmax=target_duration_sec, include_tmax=False, verbose=False)
        print(f"    [INFO] 长度截断: {current_dur:.1f}s → {target_duration_sec:.1f}s")
        return raw

    # ── 补零 ──────────────────────────────────────────────────────────
    if current_n < target_n and mode in ("pad", "crop"):
        pad_n = target_n - current_n
        data = raw.get_data()
        padded = np.pad(data, ((0, 0), (0, pad_n)), mode="constant")
        info = raw.info.copy()
        new_raw = mne.io.RawArray(padded, info, verbose=False)
        print(f"    [INFO] 长度填充: {current_dur:.1f}s → {target_duration_sec:.1f}s"
              f" (补零 {pad_n} 个采样点)")
        return new_raw

    return raw
