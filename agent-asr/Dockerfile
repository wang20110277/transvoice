# agent-asr adapter — FastAPI HTTP 服务
# 接收 UniMRCP 音频，转发 ASR 引擎，上传 MinIO
# Port: 8080

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY asradapter/ asradapter/

ENV PYTHONPATH=/app
EXPOSE 8080

CMD ["uvicorn", "asradapter.main:app", "--host", "0.0.0.0", "--port", "8080"]
