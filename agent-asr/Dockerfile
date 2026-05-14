# SenseVoice ASR Inference Server
# FunASR + SenseVoiceSmall model
# Exposes: POST /asr, GET /health
# GPU: 0

FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

WORKDIR /opt/sensevoice

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    funasr==1.2.3 \
    modelscope==1.22.0 \
    torch-complex \
    fastapi==0.115.0 \
    uvicorn[standard]==0.30.0 \
    python-multipart

COPY server.py .

RUN python -c "from modelscope import snapshot_download; snapshot_download('iic/SenseVoiceSmall', local_dir='/opt/sensevoice/models/SenseVoiceSmall')"

ENV MODEL_DIR=/opt/sensevoice/models/SenseVoiceSmall
ENV AUDIO_DIR=/data/audio
ENV PORT=10095

RUN mkdir -p ${AUDIO_DIR}
VOLUME ${AUDIO_DIR}

EXPOSE ${PORT}

CMD ["python", "server.py"]
