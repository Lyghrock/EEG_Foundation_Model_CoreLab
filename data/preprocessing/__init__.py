"""EEG 预处理模块: 压缩和精简 BIDS EEG 数据集以节省存储空间。"""

from .pipeline import preprocess_dataset

__all__ = ["preprocess_dataset"]
