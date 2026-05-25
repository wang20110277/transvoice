# agent-tts — 合并 adapter + CosyVoice2 推理引擎
# 加载 CosyVoice2 模型直接推理，无需独立引擎进程
# GPU: 1  Port: 8081

FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

# 安装 CosyVoice 运行时
RUN git clone --depth 1 https://github.com/FunAudioLLM/CosyVoice.git /opt/cosyvoice/runtime
RUN pip install --no-cache-dir -r /opt/cosyvoice/runtime/requirements.txt || true

COPY ttsadapter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ttsadapter/ ttsadapter/

# 下载 CosyVoice3-0.5B 模型
RUN python -c "from modelscope import snapshot_download; snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir='/opt/cosyvoice/models/CosyVoice3-0.5B')"

ENV MODEL_DIR=/opt/cosyvoice/models/CosyVoice3-0.5B
ENV COSYVOICE_RUNTIME=/opt/cosyvoice/runtime
ENV TTS_CACHE_DIR=/data/tts_cache
ENV PYTHONPATH=/app

RUN mkdir -p ${TTS_CACHE_DIR}
VOLUME ${TTS_CACHE_DIR}

EXPOSE 8081 50052

CMD ["uvicorn", "ttsadapter.main:app", "--host", "0.0.0.0", "--port", "8081"]
