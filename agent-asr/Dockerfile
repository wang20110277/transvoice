# agent-asr — 合并 adapter + SenseVoice 推理引擎
# 加载 FunASR 模型直接推理，无需独立引擎进程
# GPU: 0  Port: 8080

FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

COPY asradapter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY asradapter/ asradapter/

# 下载 SenseVoiceSmall 模型
RUN python -c "from modelscope import snapshot_download; snapshot_download('iic/SenseVoiceSmall', local_dir='/opt/sensevoice/models/SenseVoiceSmall')"

ENV MODEL_DIR=/opt/sensevoice/models/SenseVoiceSmall
ENV PYTHONPATH=/app
EXPOSE 8080 50051

CMD ["uvicorn", "asradapter.main:app", "--host", "0.0.0.0", "--port", "8080"]
