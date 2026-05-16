# Qwen3.5-9B LLM via vLLM
# OpenAI-compatible server
# Exposes: POST /v1/chat/completions, POST /v1/embeddings, GET /health
# GPU: 2

FROM vllm/vllm-openai:latest

# Model will be downloaded at build time
# Mount a host cache volume to avoid re-downloading on rebuild:
#   -v ~/.cache/huggingface:/root/.cache/huggingface

ARG MODEL_NAME=Qwen/Qwen3.5-9B

# Pre-download model (optional — remove to download at runtime)
RUN --mount=type=cache,target=/root/.cache/huggingface \
    python -c "from huggingface_hub import snapshot_download; snapshot_download('${MODEL_NAME}')"

ENV MODEL_NAME=${MODEL_NAME}
ENV PORT=8083
ENV MAX_MODEL_LEN=4096
ENV GPU_MEMORY_UTILIZATION=0.9

EXPOSE ${PORT}

CMD ["--model", "Qwen/Qwen3.5-9B", \
     "--served-model-name", "qwen3.5:9b", \
     "--host", "0.0.0.0", \
     "--port", "8083", \
     "--max-model-len", "4096", \
     "--gpu-memory-utilization", "0.9", \
     "--trust-remote-code"]
