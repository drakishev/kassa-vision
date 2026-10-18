#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
export CUDA_VISIBLE_DEVICES="${ANNOTATION_GPU:?set a free GPU}"
export HF_HOME="${HF_HOME:-$PWD/backend/cache/huggingface}" HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export VLLM_NO_USAGE_STATS=1 DO_NOT_TRACK=1 OMP_NUM_THREADS=6 TOKENIZERS_PARALLELISM=false
exec "${ANNOTATION_VLLM_BIN:-vllm}" serve "${KASSA_VLM_CHECKPOINT:-$PWD/backend/models/vlm}" \
 --host 127.0.0.1 --port "${ANNOTATION_VLM_PORT:-18770}" --served-model-name annotation-27b \
 --dtype bfloat16 --max-model-len "${ANNOTATION_VLM_MAX_LEN:-49152}" --gpu-memory-utilization "${ANNOTATION_VLM_MEMORY:-0.85}" --max-num-seqs 2 \
 --limit-mm-per-prompt '{"image":40,"video":0}' --no-enable-prefix-caching --mm-processor-cache-gb 0
