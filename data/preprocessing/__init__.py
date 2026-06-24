"""EEG 预处理模块: 压缩和精简 BIDS EEG 数据集以节省存储空间。"""

from .channel import STANDARD_EEG_1020, align_channels, select_eeg_channels
from .length import align_length
from .pipeline import preprocess_dataset

__all__ = [
    "preprocess_dataset",
    "select_eeg_channels",
    "align_channels",
    "align_length",
    "STANDARD_EEG_1020",
]
