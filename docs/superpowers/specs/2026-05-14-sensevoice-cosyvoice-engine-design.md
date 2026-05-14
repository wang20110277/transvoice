# SenseVoice ASR + CosyVoice TTS Engine Extension Design

## Overview

Extend `mrcp-asr` and `mrcp-tts` adapter services to support SenseVoice ASR and CosyVoice TTS as new pluggable engines, coexisting with the existing VibeVoice engines. Both new engines call external model inference services via HTTP API (FunASR Server and CosyVoice Server).

## Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Engine coexistence | New engines alongside VibeVoice | Config-driven switching via YAML |
| SenseVoice mode | Offline/batch recognition | Matches current UniMRCP HTTP POST pattern |
| CosyVoice voice mode | Built-in voices only | No voice cloning needed |
| Business type profiles | Reuse existing 3 types (customer_service/collection/marketing) | Mapped to CosyVoice built-in voices |
| Deployment | API calling (adapter → HTTP → model server) | Production high-concurrency, independent scaling |

## 1. SenseVoice ASR Engine

### Directory Structure

```
mrcp-asr/adapter/engines/sensevoice/
├── __init__.py
└── engine.py          # SenseVoiceASREngine
```

### Implementation

- **Inherits `ASREngine` ABC**: implements `recognize(audio_stream, params) -> ASRResult` and `health_check() -> bool`
- **Calls FunASR Server API**: POST audio to FunASR Server HTTP endpoint, returns recognition text
- **Exports `Engine` alias** for reflection loader

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `SENSEVOICE_API_URL` | `http://127.0.0.1:10095` | FunASR Server address |
| `SENSEVOICE_TIMEOUT` | `30` | Request timeout (seconds) |
| `SENSEVOICE_LANGUAGE` | `zh` | Recognition language |
| `SENSEVOICE_MAX_CONCURRENT` | `50` | Max concurrent requests |

### Behavior

- `recognize()`: forward audio bytes to FunASR Server via httpx AsyncClient, parse response into `ASRResult`
- `health_check()`: GET FunASR Server health endpoint, return True/False
- Concurrency control via `asyncio.Semaphore`
- Retry: httpx retry on transient errors (2 retries, exponential backoff)
- Audio format: UniMRCP sends PCM/WAV, FunASR Server accepts WAV directly

## 2. CosyVoice TTS Engine

### Directory Structure

```
mrcp-tts/adapter/engines/cosyvoice/
├── __init__.py
└── engine.py          # CosyVoiceTTSEngine
```

### Implementation

- **Inherits `TTSEngine` ABC**: implements `synthesize(text, params) -> TTSResult` and `health_check() -> bool`
- **Calls CosyVoice Server API**: POST text + voice params, returns audio bytes
- **Exports `Engine` alias** for reflection loader

### Business Type Voice Mapping

| biz_type | CosyVoice Voice | Character |
|----------|----------------|-----------|
| `customer_service` | CosyVoice soft female voice | Gentle, patient |
| `collection` | CosyVoice serious male voice | Firm, authoritative |
| `marketing` | CosyVoice lively female voice | Enthusiastic, engaging |

Mapping stored in `BIZ_TYPE_PROFILES` dict, same pattern as VibeVoice engine.

### Configuration (Environment Variables)

| Variable | Default | Description |
|----------|---------|-------------|
| `COSYVOICE_API_URL` | `http://127.0.0.1:10096` | CosyVoice Server address |
| `COSYVOICE_TIMEOUT` | `30` | Request timeout (seconds) |
| `COSYVOICE_MAX_CONCURRENT` | `30` | Max concurrent requests |

### Behavior

- `synthesize()`: check file cache first (SHA256 key by voice_id + text), cache miss → call CosyVoice Server API → cache result
- Cache path: `/data/tts_cache/{biz_type}/{hash}.wav`
- `health_check()`: GET CosyVoice Server health endpoint
- Concurrency control via `asyncio.Semaphore`
- Audio format: CosyVoice Server returns WAV, pass through directly

## 3. Configuration Changes

### mrcp-asr/adapter/config.yaml

```yaml
engine:
  asr: sensevoice
```

### mrcp-tts/adapter/config.yaml

```yaml
engine:
  tts: cosyvoice
```

### New Systemd Services

- `mrcp-asr/deploy/sensevoice-asr.service`: adapter on port 8080, env `SENSEVOICE_API_URL`
- `mrcp-tts/deploy/cosyvoice-tts.service`: adapter on port 8081, env `COSYVOICE_API_URL`

### No Changes Required

- UniMRCP server config — still POST to adapter on same ports (8080/8081)
- FreeSWITCH config — same MRCP profiles
- Orchestrator — same ESL commands

## 4. Testing

### mrcp-asr/tests/engines/sensevoice/test_engine.py

- Inheritance: `SenseVoiceASREngine` is subclass of `ASREngine`
- `health_check()`: success and failure (mock httpx)
- `recognize()`: successful recognition, timeout retry, server unavailable
- Concurrency: Semaphore limits respected

### mrcp-tts/tests/engines/cosyvoice/test_engine.py

- Inheritance: `CosyVoiceTTSEngine` is subclass of `TTSEngine`
- `health_check()`: success and failure (mock httpx)
- `synthesize()`: all 3 business type voice mappings, cache hit/miss
- Error handling: CosyVoice Server unavailable

All tests mock external HTTP calls — no dependency on real model services.

## 5. Data Flow (End-to-End)

```
FreeSWITCH → mod_unimrcp → UniMRCP Server (port 8060)
    │
    ├─ ASR → HTTP POST → mrcp-asr adapter (port 8080)
    │              └─ SenseVoiceASREngine
    │                  └─ httpx → FunASR Server (port 10095, GPU)
    │
    └─ TTS → HTTP POST → mrcp-tts adapter (port 8081)
                  └─ CosyVoiceTTSEngine
                      ├─ cache hit → return cached WAV
                      └─ cache miss → httpx → CosyVoice Server (port 10096, GPU)
```

Adapter services are CPU-only (no GPU). Model inference servers handle GPU workload independently and can scale horizontally.
