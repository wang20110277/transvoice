# CosyVoice TTS Inference Server
# CosyVoice2-0.5B model with FastAPI runtime
# Exposes: POST /tts, GET /health
# GPU: 1

FROM pytorch/pytorch:2.1.2-cuda12.1-cudnn8-runtime

WORKDIR /opt/cosyvoice

RUN apt-get update && \
    apt-get install -y --no-install-recommends git ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    modelscope==1.22.0 \
    fastapi==0.115.0 \
    uvicorn[standard]==0.30.0 \
    soundfile \
    pydub

# Install CosyVoice runtime
RUN git clone --depth 1 https://github.com/FunAudioLLM/CosyVoice.git /opt/cosyvoice/runtime

WORKDIR /opt/cosyvoice/runtime

RUN pip install --no-cache-dir -r requirements.txt || true

# Download CosyVoice2-0.5B model
RUN python -c "from modelscope import snapshot_download; snapshot_download('iic/CosyVoice2-0.5B', local_dir='/opt/cosyvoice/pretrained_models/CosyVoice2-0.5B')"

ENV MODEL_DIR=/opt/cosyvoice/pretrained_models/CosyVoice2-0.5B
ENV AUDIO_DIR=/data/audio
ENV TTS_CACHE_DIR=/data/tts_cache
ENV PORT=10096

RUN mkdir -p ${AUDIO_DIR} ${TTS_CACHE_DIR}
VOLUME ${AUDIO_DIR} ${TTS_CACHE_DIR}

COPY server.py /opt/cosyvoice/runtime/server_wrapper.py

EXPOSE ${PORT}

CMD ["python", "server_wrapper.py"]
