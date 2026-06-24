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
#   - 下载完成后自动执行预处理压缩 (采样率对齐、通道对齐、长度对齐)
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
OUTPUT_DIR="/mnt/ddn/weijun/EEG/"   # ← 修改为你的存储路径

# ── 下载控制 ──────────────────────────────────
MAX_SIZE_MB=1000                        # 下载上限 (MB), 0=不限
MAX_WORKERS=8                             # 并行下载线程数 (<= CPUS_PER_TASK)

# ── 预处理对齐 ────────────────────────────────
TARGET_FS=250                             # 目标采样率 (Hz)
ALIGN_SFREQ=true                          # true=强制对齐所有文件到 TARGET_FS
STANDARD_CHANNELS=""                      # 标准通道集 (逗号分隔, 空=不启用)
                                          # 例: "Fp1,Fp2,F3,F4,C3,C4,P3,P4,O1,O2,F7,F8,T7,T8,P7,P8,Fz,Cz,Pz,FCz"
TARGET_DURATION=""                        # 目标时长秒数 (空=不启用)
LENGTH_MODE="crop"                        # 长度对齐模式: crop/truncate/pad
INTERPOLATE_CHANNELS=false                # 是否插值缺失的标准通道

# ── 后处理 ────────────────────────────────────
ENABLE_PREPROCESS=true                    # true=下载后自动预处理
REMOVE_ORIGINAL=true                      # true=预处理后删除原始文件
# ============================================

# 解析额外参数 (传递给 Python 脚本)
EXTRA_ARGS="$@"

# 自动检测脚本路径:
# 某些集群会把 sbatch 脚本复制到 spool 目录执行, 此时 BASH_SOURCE[0] 不可靠。
# 因此优先使用提交目录, 并在候选目录中查找 down_EEG.py。
SCRIPT_BASENAME="down_EEG.py"
SCRIPT_DIR=""

CANDIDATES=()
[ -n "${SLURM_SUBMIT_DIR}" ] && CANDIDATES+=("${SLURM_SUBMIT_DIR}")
CANDIDATES+=("${PWD}")
CANDIDATES+=("$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)")

for d in "${CANDIDATES[@]}"; do
    if [ -f "${d}/${SCRIPT_BASENAME}" ]; then
        SCRIPT_DIR="${d}"
        break
    fi
done

if [ -z "${SCRIPT_DIR}" ]; then
    echo "[ERROR] 未找到下载脚本: ${SCRIPT_BASENAME}"
    echo "检查过的目录:"
    for d in "${CANDIDATES[@]}"; do
        echo "  - ${d}"
    done
    exit 1
fi

SCRIPT="${SCRIPT_DIR}/${SCRIPT_BASENAME}"

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
echo "Max size: ${MAX_SIZE_MB} MB"
echo "Preprocess: ${ENABLE_PREPROCESS}"
echo "Target FS: ${TARGET_FS} Hz (align: ${ALIGN_SFREQ})"
echo "Target duration: ${TARGET_DURATION:-none} sec (mode: ${LENGTH_MODE})"
echo "Standard channels: ${STANDARD_CHANNELS:-none}"
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

# 构建命令 (使用 MB 上限)
CMD="${SCRIPT} --output-dir ${OUTPUT_DIR} --max-size-mb ${MAX_SIZE_MB} --max-workers ${MAX_WORKERS}"

# 预处理参数
if [ "${ENABLE_PREPROCESS}" = "true" ]; then
    PREPROC_FLAGS="--preprocess --target-fs ${TARGET_FS}"

    # 采样率对齐
    if [ "${ALIGN_SFREQ}" != "true" ]; then
        PREPROC_FLAGS="${PREPROC_FLAGS} --no-align-sfreq"
    fi

    # 标准通道集
    if [ -n "${STANDARD_CHANNELS}" ]; then
        PREPROC_FLAGS="${PREPROC_FLAGS} --standard-channels ${STANDARD_CHANNELS}"
        if [ "${INTERPOLATE_CHANNELS}" = "true" ]; then
            PREPROC_FLAGS="${PREPROC_FLAGS} --interpolate-channels"
        fi
    fi

    # 长度对齐
    if [ -n "${TARGET_DURATION}" ]; then
        PREPROC_FLAGS="${PREPROC_FLAGS} --target-duration ${TARGET_DURATION} --length-mode ${LENGTH_MODE}"
    fi

    # 原始文件处理
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
