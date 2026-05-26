#!/bin/bash
# =============================================================================
# sbatch_download.sh — 集群批量下载 OpenNeuro EEG 数据集
#
# 用法:
#   sbatch sbatch_download.sh                          # 启动任务
#   sbatch sbatch_download.sh --dataset ds002778       # 只下载单个数据集
#
# 说明:
#   - 不申请 GPU
#   - 分配 N 个 CPU 核心用于并行下载 (由 --cpus-per-task 控制)
#   - 下载完成后自动执行预处理压缩 (降采样、去除非EEG通道)
#   - 输出日志到 logs/ 目录
# =============================================================================

#SBATCH --job-name=openneuro_eeg
#SBATCH --output=logs/openneuro_eeg_%j.out
#SBATCH --error=logs/openneuro_eeg_%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=72:00:00
#SBATCH --gres=none

# ============================================
# 用户配置区域 (请根据实际情况修改 ↓)
# ============================================
CONDA_ENV="eeg_fm"
OUTPUT_DIR="/path/to/your/storage/EEG"   # ← 修改为你的存储路径
MAX_SIZE_GB=1000                          # 下载上限 (GB)
MAX_WORKERS=8                             # 并行下载线程数 (<= CPUS_PER_TASK)
TARGET_FS=250                             # 预处理降采样频率 (Hz)
ENABLE_PREPROCESS=true                    # true=下载后自动预处理
REMOVE_ORIGINAL=true                      # true=预处理后删除原始文件
# ============================================

# 解析额外参数 (传递给 Python 脚本)
EXTRA_ARGS="$@"

# 自动检测脚本路径
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="${SCRIPT_DIR}/down_EEG.py"

# 创建日志目录
mkdir -p "${SCRIPT_DIR}/logs"

# 打印集群信息
echo "=========================================="
echo "Job started at: $(date)"
echo "Host: $(hostname)"
echo "SLURM Job ID: ${SLURM_JOB_ID}"
echo "CPU cores: ${SLURM_CPUS_PER_TASK:-8}"
echo "Memory: ${SLURM_MEM:-32G}"
echo "Output dir: ${OUTPUT_DIR}"
echo "Max workers: ${MAX_WORKERS}"
echo "Max size: ${MAX_SIZE_GB} GB"
echo "Preprocess: ${ENABLE_PREPROCESS}"
echo "=========================================="
echo ""

# 激活 conda 环境
# --- 根据集群的 conda 初始化方式可能需调整下面这行 ---
if command -v conda &> /dev/null; then
    # 方法1: conda init 已生效
    eval "$(conda shell.bash hook)"
    conda activate "${CONDA_ENV}"
elif [ -f "/path/to/miniconda3/etc/profile.d/conda.sh" ]; then
    # 方法2: 手动 source conda.sh (请将路径替换为实际路径)
    source "/path/to/miniconda3/etc/profile.d/conda.sh"
    conda activate "${CONDA_ENV}"
else
    echo "[ERROR] 无法激活 conda 环境: ${CONDA_ENV}"
    echo "请检查 conda 安装路径并更新本脚本中的 source 路径"
    exit 1
fi

# 验证环境
echo "Using Python: $(which python3)"
python3 -c "import mne, openneuro, requests; print(f'MNE {mne.__version__}, openneuro-py {openneuro.__version__}')" 2>&1
echo ""

# 构建命令
CMD="${SCRIPT} --output-dir ${OUTPUT_DIR} --max-size ${MAX_SIZE_GB} --max-workers ${MAX_WORKERS}"

# --no-remove-original 含义: "不删除原始文件"
# REMOVE_ORIGINAL=true  → 要删除 → 不传 --no-remove-original
# REMOVE_ORIGINAL=false → 保留   → 传 --no-remove-original
PREPROC_FLAGS="--preprocess --target-fs ${TARGET_FS}"
if [ "${ENABLE_PREPROCESS}" = "true" ]; then
    if [ "${REMOVE_ORIGINAL}" != "true" ]; then
        PREPROC_FLAGS="${PREPROC_FLAGS} --no-remove-original"
    fi
    CMD="${CMD} ${PREPROC_FLAGS}"
fi

# 附加参数
if [ -n "${EXTRA_ARGS}" ]; then
    CMD="${CMD} ${EXTRA_ARGS}"
fi

echo "Running: ${CMD}"
echo "=========================================="
echo ""

# 执行
cd "${SCRIPT_DIR}"
python3 ${CMD}

EXIT_CODE=$?

echo ""
echo "=========================================="
echo "Job finished at: $(date)"
echo "Exit code: ${EXIT_CODE}"
echo "=========================================="

exit ${EXIT_CODE}
