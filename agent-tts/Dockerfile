# agent-tts adapter — FastAPI HTTP 服务
# 接收文本合成请求，转发 TTS 引擎，上传 MinIO
# Port: 8081

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ttsadapter/ ttsadapter/

ENV PYTHONPATH=/app
EXPOSE 8081

CMD ["uvicorn", "ttsadapter.main:app", "--host", "0.0.0.0", "--port", "8081"]
