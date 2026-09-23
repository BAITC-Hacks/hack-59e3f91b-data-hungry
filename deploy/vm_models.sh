#!/usr/bin/env bash
# Serve the catalog's E5 embedding model and Qwen3 reranker on the Brev GPU VM.
# Both ports are bound to VM loopback; the backend on the same VM calls them directly.
set -euo pipefail

IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:v0.22.1}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODEL_CACHE_DIR="${MODEL_CACHE_DIR:-$HOME/ekt-model-cache}"
EMBED_PORT="${EMBED_PORT:-8891}"
RERANK_PORT="${RERANK_PORT:-8892}"
MODEL_TARGET="${MODEL_TARGET:-all}"

if [[ "$MODEL_TARGET" != "all" && "$MODEL_TARGET" != "embed" && "$MODEL_TARGET" != "rerank" ]]; then
  echo "MODEL_TARGET must be all, embed, or rerank" >&2
  exit 1
fi

for value in "$EMBED_PORT" "$RERANK_PORT"; do
  if ! [[ "$value" =~ ^[0-9]+$ ]] || (( value < 1024 || value > 65535 )); then
    echo "Model ports must be unprivileged TCP ports" >&2
    exit 1
  fi
done
if [ "$EMBED_PORT" = "$RERANK_PORT" ]; then
  echo "Embedding and rerank ports must differ" >&2
  exit 1
fi
command -v docker >/dev/null
command -v nvidia-smi >/dev/null
nvidia-smi --query-gpu=name --format=csv,noheader
mkdir -p "$MODEL_CACHE_DIR"
docker pull "$IMAGE"

start_model() {
  local name="$1" port="$2" model="$3" memory="$4" length="$5"
  shift 5
  local mounts=()
  if [ "$name" = "ekt-rerank-qwen" ]; then
    mounts+=(-v "$SCRIPT_DIR/qwen3_reranker.jinja:/templates/qwen3_reranker.jinja:ro")
  fi
  if docker container inspect "$name" >/dev/null 2>&1; then
    docker rm -f "$name" >/dev/null
  fi
  docker run -d --name "$name" --restart unless-stopped --gpus all --ipc=host \
    -p "127.0.0.1:$port:8000" \
    -v "$MODEL_CACHE_DIR:/models" "${mounts[@]}" -e HF_HOME=/models \
    "$IMAGE" "$model" --host 0.0.0.0 --port 8000 \
    --runner pooling --max-model-len "$length" --gpu-memory-utilization "$memory" "$@"
}

if [[ "$MODEL_TARGET" == "all" || "$MODEL_TARGET" == "embed" ]]; then
  start_model ekt-embed-e5 "$EMBED_PORT" intfloat/multilingual-e5-large-instruct 0.20 512
fi
if [[ "$MODEL_TARGET" == "all" || "$MODEL_TARGET" == "rerank" ]]; then
  start_model ekt-rerank-qwen "$RERANK_PORT" Qwen/Qwen3-Reranker-8B 0.45 2048 \
    --hf-overrides '{"architectures":["Qwen3ForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}' \
    --chat-template /templates/qwen3_reranker.jinja
fi

echo "Embedding: http://127.0.0.1:$EMBED_PORT/v1/embeddings"
echo "Reranker: http://127.0.0.1:$RERANK_PORT/v1/rerank"
echo "Check startup with: docker logs -f ekt-embed-e5; docker logs -f ekt-rerank-qwen"
