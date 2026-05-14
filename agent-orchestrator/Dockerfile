# agent-orchestrator — FastAPI HTTP + LangGraph 7-node 管线
# 接收 ASR 文本，驱动 LLM 对话决策，返回 TTS 音频
# Port: 8000
# 依赖: PostgreSQL (pgvector), Redis, MinIO, LLM/ASR/TTS 引擎

FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

ENV PYTHONPATH=/app/src
EXPOSE 8000

# 启动前自动迁移数据库，然后启动服务
CMD ["sh", "-c", "alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000"]
