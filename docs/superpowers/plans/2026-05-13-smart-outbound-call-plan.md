# 智能外呼系统 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建智能外呼系统完整实现，从基础设施到监控运维，8 个 Phase 分层自底向上交付。

**Architecture:** 分层架构：FreeSWITCH(UniMRCP) → Python ASR/TTS 适配层 → Orchestrator(ESL 事件驱动 + LangGraph 状态机) → LLM(Qwen) + 记忆(Redis/PG/mem0) + 身份核验(MCP)。ASR/TTS/LLM 均为可插拔引擎。

**Tech Stack:** Python 3.12, FastAPI, LangGraph, LangChain, ESL, UniMRCP(C), PostgreSQL 17 + pgvector, Redis, MinIO, mem0, Prometheus, Grafana, systemd

**Spec:** `docs/superpowers/specs/2026-05-13-smart-outbound-call-design.md`

---

## Phase 1: 基础设施部署

### Task 1: PG17 DDL 脚本

**Files:**
- Create: `deploy/init_db.sql`

- [ ] **Step 1: 创建 DDL 脚本**

```sql
-- deploy/init_db.sql
-- 智能外呼系统 数据库初始化脚本
-- Schema: callbot
-- 依赖: PostgreSQL 17 + pgvector 扩展

-- ========================================
-- 1. 创建 Schema 和扩展
-- ========================================
CREATE SCHEMA IF NOT EXISTS callbot;          -- 业务专属 schema
CREATE EXTENSION IF NOT EXISTS vector;        -- pgvector 向量检索扩展

-- ========================================
-- 2. 通话会话表（事实主表）
--    记录每通通话的完整生命周期
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_session (
  call_id            UUID PRIMARY KEY,        -- 通话唯一标识（系统生成）
  fs_uuid            UUID UNIQUE NOT NULL,    -- FreeSWITCH 会话唯一标识
  biz_type           TEXT NOT NULL CHECK (biz_type IN ('customer_service','collection','marketing')),  -- 业务类型：客服/催收/营销
  task_id            TEXT,                     -- 外呼任务 ID（来自任务调度系统）
  core_user_id       TEXT NOT NULL,           -- 核心用户 ID（来自用户中心）
  phone_hash         TEXT NOT NULL,           -- 手机号加盐哈希（不存明文）
  user_key           TEXT NOT NULL,           -- 复合用户标识：core_user_id:phone_hash
  phone_masked       TEXT,                     -- 脱敏手机号（如 138****1234）
  start_ts           TIMESTAMPTZ NOT NULL,    -- 通话开始时间
  end_ts             TIMESTAMPTZ,             -- 通话结束时间（挂断时更新）
  result_code        TEXT,                     -- 通话结果编码（normal_end/user_busy/no_answer 等）
  hangup_cause       TEXT,                     -- 挂机原因（对应 SIP Hangup-Cause）
  identity_verified  BOOLEAN NOT NULL DEFAULT FALSE,  -- 身份核验是否通过
  verify_attempts    INT NOT NULL DEFAULT 0,  -- 核验尝试次数
  recording_notice_played BOOLEAN NOT NULL DEFAULT FALSE,  -- 录音告知是否已播放（合规关键）
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()  -- 记录创建时间
);

-- 索引：按业务类型 + 用户 + 时间范围查询通话历史
CREATE INDEX IF NOT EXISTS idx_call_session_biz_user_start
  ON callbot.call_session (biz_type, user_key, start_ts DESC);
-- 索引：按任务维度查询通话记录
CREATE INDEX IF NOT EXISTS idx_call_session_task_start
  ON callbot.call_session (biz_type, task_id, start_ts DESC);

-- ========================================
-- 3. 逐轮对话表（按月分区）
--    记录每通通话中的每一轮用户/助手交互
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_turn (
  turn_id        BIGSERIAL,                   -- 轮次自增 ID
  call_id        UUID NOT NULL,               -- 关联通话会话
  fs_uuid        UUID NOT NULL,               -- FreeSWITCH 会话标识（便于排查）
  biz_type       TEXT NOT NULL,               -- 业务类型（冗余，加速查询）
  user_key       TEXT NOT NULL,               -- 复合用户标识
  role           TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),  -- 角色：用户/助手/系统/工具
  text           TEXT,                         -- 对话文本内容
  asr_conf       REAL,                         -- ASR 识别置信度（0.0~1.0）
  start_ms       INT,                          -- 轮次开始毫秒偏移（相对通话开始）
  end_ms         INT,                          -- 轮次结束毫秒偏移
  ts             TIMESTAMPTZ NOT NULL,         -- 时间戳（分区键）
  PRIMARY KEY (turn_id, ts)
) PARTITION BY RANGE (ts);

-- 索引：按通话维度查询所有轮次
CREATE INDEX IF NOT EXISTS idx_call_turn_call
  ON callbot.call_turn (call_id, ts);
-- 索引：按用户维度查询对话历史
CREATE INDEX IF NOT EXISTS idx_call_turn_biz_user_ts
  ON callbot.call_turn (biz_type, user_key, ts DESC);

-- 按月分区（示例：2026年5月、6月）
CREATE TABLE IF NOT EXISTS callbot.call_turn_202605
  PARTITION OF callbot.call_turn
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS callbot.call_turn_202606
  PARTITION OF callbot.call_turn
  FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');

-- ========================================
-- 4. 事件流表（按月分区）
--    记录通话生命周期中的所有事件（状态变更、告警、动作等）
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_event (
  event_id      BIGSERIAL,                    -- 事件自增 ID
  call_id       UUID NOT NULL,                -- 关联通话会话
  fs_uuid       UUID NOT NULL,                -- FreeSWITCH 会话标识
  biz_type      TEXT NOT NULL,                -- 业务类型
  user_key      TEXT NOT NULL,                -- 复合用户标识
  event_type    TEXT NOT NULL,                -- 事件类型（如 LEGAL_NOTICE_FAILED, HANDOFF, SENSITIVE_FIELD_BLOCKED）
  payload       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 事件详情（JSON 格式，灵活扩展）
  ts            TIMESTAMPTZ NOT NULL,         -- 事件时间戳（分区键）
  PRIMARY KEY (event_id, ts)
) PARTITION BY RANGE (ts);

-- 索引：按通话维度查询事件流
CREATE INDEX IF NOT EXISTS idx_call_event_call
  ON callbot.call_event (call_id, ts);
-- 索引：按用户维度查询事件历史
CREATE INDEX IF NOT EXISTS idx_call_event_biz_user_ts
  ON callbot.call_event (biz_type, user_key, ts DESC);
-- 索引：按事件类型查询（用于告警统计）
CREATE INDEX IF NOT EXISTS idx_call_event_type_ts
  ON callbot.call_event (biz_type, event_type, ts DESC);

-- 按月分区（示例：2026年5月）
CREATE TABLE IF NOT EXISTS callbot.call_event_202605
  PARTITION OF callbot.call_event
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

-- ========================================
-- 5. 录音/音频产物表
--    记录所有录音文件和 TTS 音频的存储位置与元数据
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_artifact (
  artifact_id   BIGSERIAL PRIMARY KEY,        -- 产物自增 ID
  call_id       UUID NOT NULL,                -- 关联通话会话
  fs_uuid       UUID NOT NULL,                -- FreeSWITCH 会话标识
  biz_type      TEXT NOT NULL,                -- 业务类型
  user_key      TEXT NOT NULL,                -- 复合用户标识
  kind          TEXT NOT NULL,                -- 文件类型：caller_wav(主叫录音)/bot_wav(机器人录音)/mix_wav(混音)/tts_wav(TTS音频)/meta_json(元数据)
  storage       TEXT NOT NULL CHECK (storage IN ('nas','minio')),  -- 存储介质：NAS 热存/MinIO 归档
  uri           TEXT NOT NULL,                -- 文件路径或对象 key
  sha256        TEXT,                          -- 文件哈希（完整性校验）
  size_bytes    BIGINT,                        -- 文件大小（字节）
  content_type  TEXT,                          -- MIME 类型（如 audio/wav）
  ts            TIMESTAMPTZ NOT NULL DEFAULT now()  -- 创建时间
);

-- 索引：按通话+文件类型查询录音
CREATE INDEX IF NOT EXISTS idx_artifact_call
  ON callbot.call_artifact (call_id, kind);
-- 索引：按业务+时间查询录音列表
CREATE INDEX IF NOT EXISTS idx_artifact_biz_ts
  ON callbot.call_artifact (biz_type, ts DESC);

-- ========================================
-- 6. 配置快照表
--    每通通话开始时冻结 Prompt/Flow/TTS/Dialplan 版本，确保可追溯
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.config_snapshot (
  snapshot_id   BIGSERIAL PRIMARY KEY,        -- 快照自增 ID
  call_id       UUID NOT NULL,                -- 关联通话会话
  fs_uuid       UUID NOT NULL,                -- FreeSWITCH 会话标识
  biz_type      TEXT NOT NULL,                -- 业务类型
  user_key      TEXT NOT NULL,                -- 复合用户标识
  prompt_version TEXT,                         -- Prompt 模板版本号
  flow_version   TEXT,                         -- LangGraph 流程版本号
  tts_profile_version TEXT,                    -- TTS 配置版本号
  dialplan_version TEXT,                       -- 拨号计划版本号
  snapshot      JSONB NOT NULL,               -- 完整配置快照（JSON 格式）
  ts            TIMESTAMPTZ NOT NULL DEFAULT now()  -- 快照时间
);

-- 索引：按通话维度查询配置快照
CREATE INDEX IF NOT EXISTS idx_snapshot_call
  ON callbot.config_snapshot (call_id, ts DESC);

-- ========================================
-- 7. 结构化记忆表（mem0 facts）
--    存储从对话中抽取的结构化事实（用户偏好、核验状态等）
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact (
  id            BIGSERIAL PRIMARY KEY,        -- 记忆自增 ID
  biz_type      TEXT NOT NULL,                -- 业务类型（记忆按业务隔离）
  user_key      TEXT NOT NULL,                -- 复合用户标识
  fact_type     TEXT NOT NULL,                -- 事实类型（如 do_not_call/preferred_contact_time/identity_verified）
  fact_value    JSONB NOT NULL,               -- 事实内容（JSON 格式，支持复杂结构）
  confidence    REAL,                          -- 置信度（规则抽取=1.0，LLM 抽取<1.0）
  first_seen_ts TIMESTAMPTZ NOT NULL,         -- 首次发现时间
  last_seen_ts  TIMESTAMPTZ NOT NULL,         -- 最近确认时间（每次命中时更新）
  source_call_id UUID,                         -- 来源通话 ID（可追溯）
  expire_ts     TIMESTAMPTZ                    -- 过期时间（NULL=永不过期）
);

-- 索引：按用户+类型查询记忆
CREATE INDEX IF NOT EXISTS idx_mem_fact_user
  ON callbot.user_memory_fact (biz_type, user_key, fact_type);
-- 索引：按最近确认时间排序（用于召回最新记忆）
CREATE INDEX IF NOT EXISTS idx_mem_fact_lastseen
  ON callbot.user_memory_fact (biz_type, user_key, last_seen_ts DESC);

-- ========================================
-- 8. 向量记忆表（pgvector，按月分区）
--    存储对话摘要、用户异议、处理片段的向量嵌入，支持相似度召回
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector (
  id            BIGSERIAL,                    -- 向量自增 ID
  biz_type      TEXT NOT NULL,                -- 业务类型
  user_key      TEXT NOT NULL,                -- 复合用户标识
  content       TEXT NOT NULL,                -- 原始文本内容
  embedding     vector(1536) NOT NULL,        -- 向量嵌入（1536维，与 OpenAI embedding 对齐）
  tags          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 标签（如情绪/异议类型/话术有效性）
  source_call_id UUID,                         -- 来源通话 ID
  source_turn_id BIGINT,                       -- 来源轮次 ID
  ts            TIMESTAMPTZ NOT NULL,         -- 时间戳（分区键）
  PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- 索引：按用户+时间范围查询向量记忆
CREATE INDEX IF NOT EXISTS idx_mem_vec_user_ts
  ON callbot.user_memory_vector (biz_type, user_key, ts DESC);

-- 按月分区（示例：2026年5月）
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector_202605
  PARTITION OF callbot.user_memory_vector
  FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');

-- HNSW 向量索引（必须在分区上创建，支持高效近似最近邻检索）
CREATE INDEX IF NOT EXISTS idx_mem_vec_202605_hnsw
  ON callbot.user_memory_vector_202605
  USING hnsw (embedding vector_cosine_ops);   -- 余弦相似度索引
```

- [ ] **Step 2: Commit**

```bash
git add deploy/init_db.sql
git commit -m "feat: add PG17 DDL script with 8 tables, partitions, and HNSW index"
```

---

### Task 2: FreeSWITCH 安装脚本

**Files:**
- Create: `deploy/install_fs.sh`

- [ ] **Step 1: 编写 FS 安装脚本**

```bash
#!/bin/bash
# deploy/install_fs.sh
set -euo pipefail

FS_VERSION="1.10.12"
FS_DIR="/usr/local/freeswitch"
CONF_DIR="${FS_DIR}/conf"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== 安装 FreeSWITCH 依赖 ==="
apt-get update && apt-get install -y \
  build-essential automake autoconf libtool wget git \
  libncurses5-dev libssl-dev libcurl4-openssl-dev \
  libjpeg-dev libsqlite3-dev libpcre3-dev libspeexdsp-dev \
  libldns-dev libedit-dev libtiff5-dev yasm uuid-dev

echo "=== 编译安装 FreeSWITCH ==="
cd /usr/local/src
git clone -b v${FS_VERSION} https://github.com/signalwire/freeswitch.git freeswitch-${FS_VERSION}
cd freeswitch-${FS_VERSION}
./bootstrap.sh -j
# 启用必要模块
sed -i 's|#mod_unimrcp|mod_unimrcp|' modules.conf
sed -i 's|#mod_event_socket|mod_event_socket|' modules.conf
./configure --prefix=${FS_DIR}
make -j$(nproc)
make install

echo "=== 部署配置文件 ==="
cp "${PROJECT_DIR}/freeswitch/modules.conf" "${CONF_DIR}/autoload_modules/modules.conf"
cp "${PROJECT_DIR}/freeswitch/vars.xml" "${CONF_DIR}/vars.xml"
cp "${PROJECT_DIR}/freeswitch/event_socket.conf.xml" "${CONF_DIR}/autoload_configs/event_socket.conf.xml"
cp "${PROJECT_DIR}/freeswitch/unimrcp.conf.xml" "${CONF_DIR}/autoload_configs/unimrcp.conf.xml"
cp "${PROJECT_DIR}/freeswitch/dialplan/public.xml" "${CONF_DIR}/dialplan/public.xml"

echo "=== 验证 ==="
${FS_DIR}/bin/fs_cli -x "show modules" | grep -E "mod_sofia|mod_unimrcp|mod_event_socket|mod_dptools"
echo "=== FreeSWITCH 安装完成 ==="
```

- [ ] **Step 2: Commit**

```bash
git add deploy/install_fs.sh
git commit -m "feat: add FreeSWITCH install and config deployment script"
```

---

### Task 3: UniMRCP 安装脚本

**Files:**
- Create: `deploy/install_unimrcp.sh`

- [ ] **Step 1: 编写 UniMRCP 安装脚本**

```bash
#!/bin/bash
# deploy/install_unimrcp.sh
set -euo pipefail

UNIMRCP_DIR="/usr/local/unimrcp"
CONF_DIR="/etc/unimrcp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== 安装 UniMRCP 依赖 ==="
apt-get update && apt-get install -y \
  build-essential automake autoconf libtool \
  libsofia-sip-ua-dev libssl-dev libcurl4-openssl-dev

echo "=== 编译安装 UniMRCP ==="
cd /usr/local/src
git clone https://github.com/unispeech/unimrcp.git
cd unimrcp
./bootstrap
./configure --prefix=${UNIMRCP_DIR}
make -j$(nproc)
make install

echo "=== 部署配置 ==="
mkdir -p ${CONF_DIR}
cp "${PROJECT_DIR}/freeswitch/unimrcp/unimrcpserver.xml" "${CONF_DIR}/unimrcpserver.xml"

echo "=== 验证 ==="
${UNIMRCP_DIR}/bin/unimrcpserver --version
echo "=== UniMRCP 安装完成 ==="
```

- [ ] **Step 2: Commit**

```bash
git add deploy/install_unimrcp.sh
git commit -m "feat: add UniMRCP install and config deployment script"
```

---

### Task 4: 依赖服务安装脚本

**Files:**
- Create: `deploy/install_deps.sh`

- [ ] **Step 1: 编写 Redis/MinIO 安装脚本**

```bash
#!/bin/bash
# deploy/install_deps.sh
set -euo pipefail

echo "=== 安装 Redis ==="
apt-get update && apt-get install -y redis-server
systemctl enable redis-server
systemctl start redis-server
redis-cli ping

echo "=== 安装 MinIO ==="
wget -q https://dl.min.io/server/minio/release/linux-amd64/minio -O /usr/local/bin/minio
chmod +x /usr/local/bin/minio
useradd -r -s /bin/false minio || true
mkdir -p /data/minio
chown minio:minio /data/minio

# 创建 systemd 服务
cat > /etc/systemd/system/minio.service << 'EOS'
[Unit]
Description=MinIO
After=network.target

[Service]
User=minio
Group=minio
ExecStart=/usr/local/bin/minio server /data/minio --console-address ":9001"
Restart=always
Environment=MINIO_ROOT_USER=admin
Environment=MINIO_ROOT_PASSWORD=changeme123

[Install]
WantedBy=multi-user.target
EOS

systemctl daemon-reload
systemctl enable minio
systemctl start minio

echo "=== 创建 MinIO buckets ==="
sleep 3
mc alias set local http://localhost:9000 admin changeme123 2>/dev/null || true
mc mb local/rec-cs 2>/dev/null || true
mc mb local/rec-collection 2>/dev/null || true
mc mb local/rec-marketing 2>/dev/null || true

echo "=== 依赖服务安装完成 ==="
```

- [ ] **Step 2: Commit**

```bash
git add deploy/install_deps.sh
git commit -m "feat: add Redis and MinIO install script with bucket init"
```

---

### Task 5: 一键安装入口脚本

**Files:**
- Create: `deploy/install_all.sh`

- [ ] **Step 1: 编写一键安装脚本**

```bash
#!/bin/bash
# deploy/install_all.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "===== 智能外呼系统 一键安装 ====="

echo "[1/5] 安装依赖服务 (Redis + MinIO)..."
bash "${SCRIPT_DIR}/install_deps.sh"

echo "[2/5] 安装 FreeSWITCH..."
bash "${SCRIPT_DIR}/install_fs.sh"

echo "[3/5] 安装 UniMRCP..."
bash "${SCRIPT_DIR}/install_unimrcp.sh"

echo "[4/5] 初始化数据库..."
export PGHOST="${PGHOST:-127.0.0.1}"
export PGPORT="${PGPORT:-5432}"
export PGUSER="${PGUSER:-postgres}"
export PGDATABASE="${PGDATABASE:-callbot}"
psql -c "CREATE DATABASE callbot;" 2>/dev/null || true
psql -d callbot -f "${SCRIPT_DIR}/init_db.sql"

echo "[5/5] 验证安装..."
echo "--- FreeSWITCH ---"
fs_cli -x "show modules" 2>/dev/null | head -5 || echo "FreeSWITCH 未运行（需手动启动）"
echo "--- UniMRCP ---"
systemctl is-active unimrcp 2>/dev/null || echo "UniMRCP 未运行（需手动启动）"
echo "--- Redis ---"
redis-cli ping
echo "--- MinIO ---"
systemctl is-active minio 2>/dev/null || echo "MinIO 未运行"
echo "--- PostgreSQL ---"
psql -d callbot -c "\dt callbot.*" 2>/dev/null | head -15

echo "===== 安装完成 ====="
```

- [ ] **Step 2: Commit**

```bash
git add deploy/install_all.sh
git commit -m "feat: add one-click install entry script"
```

---

## Phase 2: ASR/TTS 适配层

### Task 6: ASR 抽象接口与引擎加载

**Files:**
- Create: `mrcp-asr/adapter/base.py`
- Create: `mrcp-asr/adapter/config.py`
- Create: `mrcp-asr/adapter/__init__.py`
- Create: `mrcp-asr/adapter/engines/__init__.py`
- Create: `mrcp-asr/adapter/engines/vibevoice/__init__.py`
- Test: `mrcp-asr/tests/test_base.py`
- Test: `mrcp-asr/tests/__init__.py`

- [ ] **Step 1: 编写引擎加载测试**

```python
# mrcp-asr/tests/test_base.py
import pytest
from adapter.base import ASREngine, ASRResult


def test_asr_result_creation():
    result = ASRResult(text="你好", confidence=0.95, is_final=True)
    assert result.text == "你好"
    assert result.confidence == 0.95
    assert result.is_final is True


def test_asr_engine_is_abstract():
    with pytest.raises(TypeError):
        ASREngine()


def test_load_unknown_engine_raises():
    from adapter.config import load_asr_engine
    with pytest.raises(ValueError, match="Unknown ASR engine"):
        load_asr_engine("nonexistent_engine")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd mrcp-asr && python -m pytest tests/test_base.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: 实现抽象接口**

```python
# mrcp-asr/adapter/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ASRResult:
    text: str
    confidence: float
    is_final: bool


class ASREngine(ABC):
    @abstractmethod
    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        """接收音频流，返回识别结果"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
```

```python
# mrcp-asr/adapter/config.py
import importlib
from adapter.base import ASREngine


def load_asr_engine(name: str) -> ASREngine:
    """反射加载 engines/{name}/engine.py 中的 Engine 类"""
    try:
        module = importlib.import_module(f"adapter.engines.{name}.engine")
        return module.Engine()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Unknown ASR engine: {name}") from e
```

```python
# mrcp-asr/adapter/__init__.py
```

```python
# mrcp-asr/adapter/engines/__init__.py
```

```python
# mrcp-asr/adapter/engines/vibevoice/__init__.py
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd mrcp-asr && python -m pytest tests/test_base.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mrcp-asr/adapter/ mrcp-asr/tests/
git commit -m "feat(asr): add pluggable engine abstract interface and loader"
```

---

### Task 7: ASR VibeVoice 引擎实现

**Files:**
- Create: `mrcp-asr/adapter/engines/vibevoice/engine.py`
- Test: `mrcp-asr/tests/engines/vibevoice/__init__.py`
- Test: `mrcp-asr/tests/engines/__init__.py`
- Test: `mrcp-asr/tests/engines/vibevoice/test_engine.py`

- [ ] **Step 1: 编写 VibeVoice ASR 引擎测试**

```python
# mrcp-asr/tests/engines/vibevoice/test_engine.py
import pytest
from unittest.mock import AsyncMock, patch
from adapter.engines.vibevoice.engine import VibeVoiceASREngine


@pytest.fixture
def engine():
    return VibeVoiceASREngine()


def test_engine_inherits_base():
    from adapter.base import ASREngine
    assert isinstance(engine(), ASREngine)


@pytest.mark.asyncio
async def test_health_check(engine):
    with patch.object(engine, "_model_loaded", True):
        result = await engine.health_check()
        assert result is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd mrcp-asr && python -m pytest tests/engines/vibevoice/test_engine.py -v
```

- [ ] **Step 3: 实现 VibeVoice ASR 引擎**

```python
# mrcp-asr/adapter/engines/vibevoice/engine.py
import asyncio
import logging
from adapter.base import ASREngine, ASRResult

logger = logging.getLogger(__name__)


class VibeVoiceASREngine(ASREngine):
    def __init__(self):
        self._model = None
        self._model_loaded = False
        self._semaphore = asyncio.Semaphore(50)

    async def load_model(self):
        """加载 VibeVoice ASR 模型"""
        # TODO: 替换为实际 VibeVoice 模型加载
        # from modelscope import pipeline
        # self._model = pipeline("asr", model="microsoft/VibeVoice-ASR")
        logger.info("VibeVoice ASR model loading (stub)")
        self._model_loaded = True

    async def recognize(self, audio_stream: bytes, params: dict) -> ASRResult:
        """识别音频流"""
        async with self._semaphore:
            if not self._model_loaded:
                raise RuntimeError("ASR model not loaded")

            # TODO: 替换为实际 VibeVoice 调用
            # result = self._model(audio_stream)
            # return ASRResult(text=result["text"], confidence=result["confidence"], is_final=True)

            return ASRResult(text="", confidence=0.0, is_final=True)

    async def health_check(self) -> bool:
        return self._model_loaded
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd mrcp-asr && python -m pytest tests/engines/vibevoice/test_engine.py -v
```

- [ ] **Step 5: Commit**

```bash
git add mrcp-asr/adapter/engines/vibevoice/ mrcp-asr/tests/engines/
git commit -m "feat(asr): add VibeVoice ASR engine implementation"
```

---

### Task 8: ASR FastAPI 入口

**Files:**
- Create: `mrcp-asr/adapter/main.py`
- Create: `mrcp-asr/adapter/config.yaml`
- Test: `mrcp-asr/tests/test_main.py`

- [ ] **Step 1: 编写 API 测试**

```python
# mrcp-asr/tests/test_main.py
import pytest
from httpx import AsyncClient, ASGITransport
from adapter.main import app


@pytest.mark.asyncio
async def test_healthz():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: 实现 FastAPI 入口**

```python
# mrcp-asr/adapter/main.py
import os
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, Form
from adapter.base import ASRResult
from adapter.config import load_asr_engine

logger = logging.getLogger(__name__)

engine = None


def _load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    config = _load_config()
    engine = load_asr_engine(config["engine"]["asr"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"ASR engine loaded: {config['engine']['asr']}")
    yield


app = FastAPI(title="ASR Adapter Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    healthy = await engine.health_check() if engine else False
    return {"status": "ok" if healthy else "degraded"}


@app.post("/asr/recognize")
async def recognize(audio: UploadFile, params: str = Form("{}")):
    import json
    audio_bytes = await audio.read()
    result = await engine.recognize(audio_bytes, json.loads(params))
    return {"text": result.text, "confidence": result.confidence, "is_final": result.is_final}
```

```yaml
# mrcp-asr/adapter/config.yaml
engine:
  asr: vibevoice
```

- [ ] **Step 3: 运行测试确认通过**

```bash
cd mrcp-asr && python -m pytest tests/test_main.py -v
```

- [ ] **Step 4: Commit**

```bash
git add mrcp-asr/adapter/main.py mrcp-asr/adapter/config.yaml mrcp-asr/tests/test_main.py
git commit -m "feat(asr): add FastAPI entry with pluggable engine loading"
```

---

### Task 9: TTS 适配层（同构 ASR）

**Files:**
- Create: `mrcp-tts/adapter/base.py`
- Create: `mrcp-tts/adapter/config.py`
- Create: `mrcp-tts/adapter/__init__.py`
- Create: `mrcp-tts/adapter/engines/__init__.py`
- Create: `mrcp-tts/adapter/engines/vibevoice/__init__.py`
- Create: `mrcp-tts/adapter/engines/vibevoice/engine.py`
- Create: `mrcp-tts/adapter/main.py`
- Create: `mrcp-tts/adapter/config.yaml`
- Create: `mrcp-tts/tests/__init__.py`
- Create: `mrcp-tts/tests/test_base.py`
- Create: `mrcp-tts/tests/test_main.py`

- [ ] **Step 1: 实现 TTS 抽象接口**

```python
# mrcp-tts/adapter/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TTSResult:
    audio: bytes
    content_type: str = "audio/wav"
    duration_ms: int = 0


class TTSEngine(ABC):
    @abstractmethod
    async def synthesize(self, text: str, params: dict) -> TTSResult:
        """接收文本，返回合成音频"""

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
```

```python
# mrcp-tts/adapter/config.py
import importlib
from adapter.base import TTSEngine


def load_tts_engine(name: str) -> TTSEngine:
    try:
        module = importlib.import_module(f"adapter.engines.{name}.engine")
        return module.Engine()
    except (ImportError, AttributeError) as e:
        raise ValueError(f"Unknown TTS engine: {name}") from e
```

- [ ] **Step 2: 实现 VibeVoice TTS 引擎**

```python
# mrcp-tts/adapter/engines/vibevoice/engine.py
import asyncio
import hashlib
import os
import logging
from adapter.base import TTSEngine, TTSResult

logger = logging.getLogger(__name__)

BIZ_TYPE_PROFILES = {
    "customer_service": {"voice_id": "cs_female_soft_01", "speed": 0, "volume": 0, "pitch": 0},
    "collection": {"voice_id": "col_male_serious_01", "speed": -1, "volume": 1, "pitch": -1},
    "marketing": {"voice_id": "mkt_female_lively_01", "speed": 1, "volume": 0, "pitch": 1},
}

DEFAULT_PROFILE = BIZ_TYPE_PROFILES["customer_service"]


class VibeVoiceTTSEngine(TTSEngine):
    def __init__(self):
        self._model = None
        self._model_loaded = False
        self._cache_dir = "/data/tts_cache"
        self._semaphore = asyncio.Semaphore(30)

    async def load_model(self):
        # TODO: 替换为实际 VibeVoice TTS 模型加载
        # from modelscope import pipeline
        # self._model = pipeline("tts", model="microsoft/VibeVoice-Realtime-0.5B")
        logger.info("VibeVoice TTS model loading (stub)")
        self._model_loaded = True

    def _get_profile(self, params: dict) -> dict:
        biz_type = params.get("biz_type", "customer_service")
        return BIZ_TYPE_PROFILES.get(biz_type, DEFAULT_PROFILE)

    def _cache_key(self, text: str, profile: dict) -> str:
        raw = f"{profile['voice_id']}:{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, biz_type: str, key: str) -> str:
        path = os.path.join(self._cache_dir, biz_type, f"{key}.wav")
        return path

    async def synthesize(self, text: str, params: dict) -> TTSResult:
        async with self._semaphore:
            if not self._model_loaded:
                raise RuntimeError("TTS model not loaded")

            profile = self._get_profile(params)
            biz_type = params.get("biz_type", "customer_service")
            cache_path = self._cache_path(biz_type, self._cache_key(text, profile))

            # 缓存命中
            if os.path.exists(cache_path):
                with open(cache_path, "rb") as f:
                    return TTSResult(audio=f.read())

            # TODO: 替换为实际 VibeVoice 调用
            # audio = self._model(text, **profile)
            # os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            # with open(cache_path, "wb") as f:
            #     f.write(audio)
            # return TTSResult(audio=audio)

            return TTSResult(audio=b"")

    async def health_check(self) -> bool:
        return self._model_loaded
```

- [ ] **Step 3: 实现 TTS FastAPI 入口**

```python
# mrcp-tts/adapter/main.py
import os
import json
import yaml
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Form
from fastapi.responses import Response
from adapter.config import load_tts_engine

logger = logging.getLogger(__name__)
engine = None


def _load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(config_path) as f:
        return yaml.safe_load(f)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global engine
    config = _load_config()
    engine = load_tts_engine(config["engine"]["tts"])
    if hasattr(engine, "load_model"):
        await engine.load_model()
    logger.info(f"TTS engine loaded: {config['engine']['tts']}")
    yield


app = FastAPI(title="TTS Adapter Service", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    healthy = await engine.health_check() if engine else False
    return {"status": "ok" if healthy else "degraded"}


@app.post("/tts/synthesize")
async def synthesize(text: str = Form(...), params: str = Form("{}")):
    result = await engine.synthesize(text, json.loads(params))
    return Response(content=result.audio, media_type=result.content_type)
```

```yaml
# mrcp-tts/adapter/config.yaml
engine:
  tts: vibevoice
```

- [ ] **Step 4: 编写测试**

```python
# mrcp-tts/tests/test_base.py
import pytest
from adapter.base import TTSEngine, TTSResult


def test_tts_result_creation():
    result = TTSResult(audio=b"fake_wav", content_type="audio/wav")
    assert result.audio == b"fake_wav"


def test_tts_engine_is_abstract():
    with pytest.raises(TypeError):
        TTSEngine()
```

```python
# mrcp-tts/tests/test_main.py
import pytest
from httpx import AsyncClient, ASGITransport
from adapter.main import app


@pytest.mark.asyncio
async def test_healthz():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/healthz")
        assert resp.status_code == 200
```

- [ ] **Step 5: 运行测试**

```bash
cd mrcp-tts && python -m pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add mrcp-tts/
git commit -m "feat(tts): add pluggable TTS adapter with VibeVoice engine"
```

---

### Task 10: ASR/TTS systemd 服务文件

**Files:**
- Create: `mrcp-asr/deploy/vibevoice-asr.service`
- Create: `mrcp-tts/deploy/vibevoice-tts.service`

- [ ] **Step 1: 创建 systemd 文件**

```ini
# mrcp-asr/deploy/vibevoice-asr.service
[Unit]
Description=VibeVoice ASR Adapter Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mrcp-asr/adapter
ExecStart=/opt/mrcp-asr/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
LimitNOFILE=1000000
Environment=CUDA_VISIBLE_DEVICES=0

[Install]
WantedBy=multi-user.target
```

```ini
# mrcp-tts/deploy/vibevoice-tts.service
[Unit]
Description=VibeVoice TTS Adapter Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/mrcp-tts/adapter
ExecStart=/opt/mrcp-tts/venv/bin/uvicorn main:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
LimitNOFILE=1000000
Environment=CUDA_VISIBLE_DEVICES=1

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add mrcp-asr/deploy/ mrcp-tts/deploy/
git commit -m "feat: add systemd service files for ASR and TTS adapters"
```

---

## Phase 3: Orchestrator 核心

### Task 11: 项目初始化与统一配置

**Files:**
- Create: `agent-orchestrator/config.py`
- Create: `agent-orchestrator/requirements.txt`
- Create: `agent-orchestrator/tests/__init__.py`
- Test: `agent-orchestrator/tests/test_config.py`

- [ ] **Step 1: 编写配置测试**

```python
# agent-orchestrator/tests/test_config.py
from config import Settings


def test_default_settings():
    s = Settings()
    assert s.fs_esl_host == "127.0.0.1"
    assert s.fs_esl_port == 8021
    assert s.redis_url.startswith("redis://")
    assert s.pg_dsn.startswith("postgresql://")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_config.py -v
```

- [ ] **Step 3: 实现配置模块**

```python
# agent-orchestrator/config.py
import os
from dataclasses import dataclass, field


@dataclass
class Settings:
    # FreeSWITCH ESL
    fs_esl_host: str = os.getenv("FS_ESL_HOST", "127.0.0.1")
    fs_esl_port: int = int(os.getenv("FS_ESL_PORT", "8021"))
    fs_esl_password: str = os.getenv("FS_ESL_PASSWORD", "ClueCon")

    # Redis
    redis_url: str = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")

    # PostgreSQL
    pg_dsn: str = os.getenv("PG_DSN", "postgresql://postgres@127.0.0.1:5432/callbot")

    # MinIO
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "admin")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "changeme123")

    # 业务
    biz_types: list = field(default_factory=lambda: ["customer_service", "collection", "marketing"])
    handoff_extension: str = os.getenv("HANDOFF_EXTENSION", "1001")
    legal_notice_file: str = os.getenv("LEGAL_NOTICE_FILE", "/usr/local/freeswitch/sounds/legal_notice.wav")

    # 超时
    llm_timeout_sec: float = float(os.getenv("LLM_TIMEOUT_SEC", "3.0"))
    silence_timeout_sec: int = int(os.getenv("SILENCE_TIMEOUT_SEC", "5"))
    max_silence_count: int = int(os.getenv("MAX_SILENCE_COUNT", "3"))

    # LLM
    llm_engine: str = os.getenv("LLM_ENGINE", "qwen")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8080")

    # MCP
    mcp_server_url: str = os.getenv("MCP_SERVER_URL", "http://127.0.0.1:9090")


settings = Settings()
```

```txt
# agent-orchestrator/requirements.txt
fastapi>=0.110
uvicorn>=0.29
python-ESL>=0.1
redis>=5.0
psycopg[binary]>=3.1
langchain>=0.2
langgraph>=0.2
mem0ai>=0.1
pyyaml>=6.0
httpx>=0.27
prometheus-client>=0.20
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_config.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/config.py agent-orchestrator/requirements.txt agent-orchestrator/tests/
git commit -m "feat(orchestrator): add unified config module with env overrides"
```

---

### Task 12: 通话状态管理

**Files:**
- Create: `agent-orchestrator/call_state.py`
- Test: `agent-orchestrator/tests/test_call_state.py`

- [ ] **Step 1: 编写状态管理测试**

```python
# agent-orchestrator/tests/test_call_state.py
import pytest
from call_state import CallState, CallStateManager


def test_call_state_defaults():
    state = CallState(fs_uuid="test-uuid")
    assert state.biz_type == ""
    assert state.turn_count == 0
    assert state.silence_count == 0
    assert state.identity_verified is False


def test_manager_set_get():
    mgr = CallStateManager()
    state = CallState(fs_uuid="uuid-1", biz_type="marketing")
    mgr.set("uuid-1", state)
    assert mgr.get("uuid-1").biz_type == "marketing"


def test_manager_remove():
    mgr = CallStateManager()
    mgr.set("uuid-1", CallState(fs_uuid="uuid-1"))
    mgr.remove("uuid-1")
    assert mgr.get("uuid-1") is None


def test_manager_list():
    mgr = CallStateManager()
    mgr.set("a", CallState(fs_uuid="a"))
    mgr.set("b", CallState(fs_uuid="b"))
    assert len(mgr.list_active()) == 2
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_call_state.py -v
```

- [ ] **Step 3: 实现 CallState**

```python
# agent-orchestrator/call_state.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CallState:
    fs_uuid: str
    biz_type: str = ""
    task_id: str = ""
    core_user_id: str = ""
    phone_hash: str = ""
    user_key: str = ""
    phone_masked: str = ""
    status: str = "created"
    turn_count: int = 0
    silence_count: int = 0
    asr_fail_count: int = 0
    llm_fail_count: int = 0
    identity_verified: bool = False
    recording_notice_played: bool = False
    recording_path: str = ""
    start_time: float = 0.0
    answer_time: float = 0.0
    last_action: Optional[dict] = None
    created_at: datetime = field(default_factory=datetime.now)


class CallStateManager:
    def __init__(self):
        self._states: dict[str, CallState] = {}

    def get(self, fs_uuid: str) -> CallState | None:
        return self._states.get(fs_uuid)

    def set(self, fs_uuid: str, state: CallState):
        self._states[fs_uuid] = state

    def remove(self, fs_uuid: str) -> CallState | None:
        return self._states.pop(fs_uuid, None)

    def list_active(self) -> list[CallState]:
        return list(self._states.values())
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_call_state.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/call_state.py agent-orchestrator/tests/test_call_state.py
git commit -m "feat(orchestrator): add CallState dataclass and in-memory state manager"
```

---

### Task 13: FS 动作封装

**Files:**
- Create: `agent-orchestrator/fs_actions.py`
- Test: `agent-orchestrator/tests/test_fs_actions.py`

- [ ] **Step 1: 编写动作封装测试**

```python
# agent-orchestrator/tests/test_fs_actions.py
import pytest
from unittest.mock import MagicMock
from fs_actions import FSActions, TTSProfileMap


def test_tts_profile_mapping():
    assert TTSProfileMap.get("customer_service") == "tts_customer_service_v1"
    assert TTSProfileMap.get("collection") == "tts_collection_v1"
    assert TTSProfileMap.get("marketing") == "tts_marketing_v1"


def test_asr_profile():
    assert TTSProfileMap.get_asr() == "asr_default_v1"


def test_play_legal_notice_calls_api():
    conn = MagicMock()
    conn.api.return_value = "+OK"
    actions = FSActions(conn)
    result = actions.play_legal_notice("uuid-1")
    assert result is True
    conn.api.assert_called_once()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_fs_actions.py -v
```

- [ ] **Step 3: 实现 FSActions**

```python
# agent-orchestrator/fs_actions.py
import logging
from config import settings

logger = logging.getLogger(__name__)


class TTSProfileMap:
    _MAP = {
        "customer_service": "tts_customer_service_v1",
        "collection": "tts_collection_v1",
        "marketing": "tts_marketing_v1",
    }

    @classmethod
    def get(cls, biz_type: str) -> str:
        return cls._MAP.get(biz_type, "tts_customer_service_v1")

    @classmethod
    def get_asr(cls) -> str:
        return "asr_default_v1"


class FSActions:
    def __init__(self, conn):
        self.conn = conn

    def play_legal_notice(self, uuid: str) -> bool:
        result = self.conn.api(f"uuid_playback {uuid} {settings.legal_notice_file}")
        return str(result) == "+OK"

    def start_recording(self, uuid: str, rec_path: str) -> bool:
        import os
        os.makedirs(rec_path, exist_ok=True)
        self.conn.api(f"uuid_record {uuid} start {rec_path}/caller.wav 48000 16")
        self.conn.api(f"uuid_record {uuid} start {rec_path}/bot.wav 48000 16")
        return True

    def stop_recording(self, uuid: str):
        self.conn.api(f"uuid_record {uuid} stop all")

    def start_detect_speech(self, uuid: str):
        profile = TTSProfileMap.get_asr()
        self.conn.api(f"uuid_detect_speech {uuid} unimrcp://{profile} builtin:grammar:digits")

    def stop_detect_speech(self, uuid: str):
        self.conn.api(f"uuid_detect_speech {uuid} stop")

    def tts_speak(self, uuid: str, biz_type: str, text: str):
        profile = TTSProfileMap.get(biz_type)
        self.conn.api(f"uuid_playback {uuid} say:{text}^^{profile}")

    def transfer(self, uuid: str, extension: str = None):
        ext = extension or settings.handoff_extension
        self.conn.api(f"uuid_transfer {uuid} loopback/{ext}")

    def hangup(self, uuid: str, reason: str = "NORMAL_CLEARING"):
        self.conn.api(f"uuid_kill {uuid} {reason}")
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_fs_actions.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/fs_actions.py agent-orchestrator/tests/test_fs_actions.py
git commit -m "feat(orchestrator): add FSActions wrapper for ESL commands"
```

---

### Task 14: 事件处理器

**Files:**
- Create: `agent-orchestrator/event_handlers.py`
- Create: `agent-orchestrator/prompts/customer_service.yaml`
- Create: `agent-orchestrator/prompts/collection.yaml`
- Create: `agent-orchestrator/prompts/marketing.yaml`
- Test: `agent-orchestrator/tests/test_event_handlers.py`

- [ ] **Step 1: 编写事件处理测试**

```python
# agent-orchestrator/tests/test_event_handlers.py
import pytest
from unittest.mock import MagicMock, patch
from event_handlers import EventDispatcher
from call_state import CallStateManager


@pytest.fixture
def dispatcher():
    mgr = CallStateManager()
    conn = MagicMock()
    actions = MagicMock()
    return EventDispatcher(mgr, conn, actions)


def test_handle_channel_create(dispatcher):
    event = {"Unique-ID": "uuid-1", "Call-Direction": "outbound"}
    dispatcher.handle_channel_create(event)
    state = dispatcher.state_mgr.get("uuid-1")
    assert state is not None
    assert state.status == "created"


def test_handle_channel_hangup_cleans_state(dispatcher):
    dispatcher.state_mgr.set("uuid-1", MagicMock(fs_uuid="uuid-1", status="answered", start_time=0))
    event = {"Unique-ID": "uuid-1", "Hangup-Cause": "NORMAL_CLEARING"}
    dispatcher.handle_channel_hangup(event)
    assert dispatcher.state_mgr.get("uuid-1") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_event_handlers.py -v
```

- [ ] **Step 3: 实现事件处理器**

```python
# agent-orchestrator/event_handlers.py
import time
import json
import logging
from datetime import datetime
from call_state import CallState, CallStateManager
from fs_actions import FSActions, TTSProfileMap
from config import settings

logger = logging.getLogger(__name__)

# Phase 3 临时规则引擎
RULES = {
    "marketing": "您好，感谢您的接听，请问有什么可以帮助您的？",
    "customer_service": "您好，请问有什么可以帮您？",
    "collection": "您好，这里有一笔账单需要确认。",
}
DEFAULT_REPLY = "抱歉，我没听清楚，请您再说一遍。"


class EventDispatcher:
    def __init__(self, state_mgr: CallStateManager, conn, actions: FSActions):
        self.state_mgr = state_mgr
        self.conn = conn
        self.actions = actions

    def dispatch(self, event: dict):
        event_name = event.get("Event-Name", "")
        handler = {
            "CHANNEL_CREATE": self.handle_channel_create,
            "CHANNEL_ANSWER": self.handle_channel_answer,
            "DETECTED_SPEECH": self.handle_detected_speech,
            "CHANNEL_HANGUP": self.handle_channel_hangup,
            "CHANNEL_HANGUP_COMPLETE": self.handle_channel_hangup,
        }.get(event_name)
        if handler:
            handler(event)

    def handle_channel_create(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = CallState(
            fs_uuid=fs_uuid,
            call_direction=event.get("Call-Direction", ""),
            status="created",
            start_time=time.time(),
        )
        self.state_mgr.set(fs_uuid, state)
        logger.info(f"[{fs_uuid}] CHANNEL_CREATE")

    def handle_channel_answer(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = self.state_mgr.get(fs_uuid)
        if not state:
            logger.error(f"[{fs_uuid}] CHANNEL_ANSWER: state not found")
            return

        state.status = "answered"
        state.answer_time = time.time()

        # 设置业务变量
        state.biz_type = event.get("variable_biz_type", "marketing")
        state.task_id = event.get("variable_task_id", "")
        state.core_user_id = event.get("variable_core_user_id", "")
        state.phone_hash = event.get("variable_phone_hash", "")
        state.user_key = f"{state.core_user_id}:{state.phone_hash}" if state.core_user_id else ""

        # 播放录音告知
        try:
            result = self.actions.play_legal_notice(fs_uuid)
            state.recording_notice_played = result
            if not result:
                logger.error(f"[{fs_uuid}] 录音告知播放失败")
        except Exception as e:
            logger.exception(f"[{fs_uuid}] 录音告知异常: {e}")
            state.recording_notice_played = False

        # 启动录音
        try:
            date_str = datetime.now().strftime("%Y/%m/%d")
            rec_path = f"/nas/rec/{state.biz_type}/{date_str}/{fs_uuid}"
            self.actions.start_recording(fs_uuid, rec_path)
            state.recording_path = rec_path
        except Exception as e:
            logger.exception(f"[{fs_uuid}] 录音启动失败: {e}")

        # 启动 detect_speech
        try:
            self.actions.start_detect_speech(fs_uuid)
            state.status = "listening"
        except Exception as e:
            logger.exception(f"[{fs_uuid}] detect_speech 启动失败: {e}")

        logger.info(f"[{fs_uuid}] CHANNEL_ANSWER: biz_type={state.biz_type}")

    def handle_detected_speech(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = self.state_mgr.get(fs_uuid)
        if not state or state.status != "listening":
            return

        speech_text = event.get("speech", "") or ""
        if not speech_text.strip():
            logger.debug(f"[{fs_uuid}] DETECTED_SPEECH: empty text")
            return

        logger.info(f"[{fs_uuid}] 用户发言: {speech_text[:50]}")
        state.silence_count = 0
        state.turn_count += 1

        # Phase 3: 规则引擎
        reply = RULES.get(state.biz_type, DEFAULT_REPLY)

        # 停止 detect_speech，播放 TTS
        self.actions.stop_detect_speech(fs_uuid)
        self.actions.tts_speak(fs_uuid, state.biz_type, reply)

        # 恢复 detect_speech
        state.status = "listening"
        self.actions.start_detect_speech(fs_uuid)

    def handle_channel_hangup(self, event: dict):
        fs_uuid = event["Unique-ID"]
        state = self.state_mgr.remove(fs_uuid)
        if not state:
            return

        hangup_cause = event.get("Hangup-Cause", "")
        duration = time.time() - state.start_time if state.start_time else 0
        logger.info(f"[{fs_uuid}] HANGUP: cause={hangup_cause}, duration={duration:.1f}s")

        try:
            self.actions.stop_detect_speech(fs_uuid)
        except Exception:
            pass
        try:
            self.actions.stop_recording(fs_uuid)
        except Exception:
            pass
```

- [ ] **Step 4: 创建 Prompt 模板**

```yaml
# agent-orchestrator/prompts/customer_service.yaml
system: |
  你是一名客服AI助手。语气温柔、专业、有耐心。
  回答用户问题，必要时转接人工。
response_schema:
  action: {type: string, enum: [say, ask, handoff, end]}
  text: {type: string}
  intent: {type: string}
max_reply_length: 200
```

```yaml
# agent-orchestrator/prompts/collection.yaml
system: |
  你是一名催收专员AI助手。语气专业、不威胁。
  严格规则：仅在身份核验通过后才能提及具体欠款金额。
  每次回复不超过50字。
response_schema:
  action: {type: string, enum: [say, ask, handoff, end]}
  text: {type: string}
  intent: {type: string}
max_reply_length: 50
```

```yaml
# agent-orchestrator/prompts/marketing.yaml
system: |
  你是一名营销AI助手。语气热情、活力、有感染力。
  介绍产品优势，引导用户兴趣。每次回复不超过80字。
response_schema:
  action: {type: string, enum: [say, ask, handoff, end]}
  text: {type: string}
  intent: {type: string}
max_reply_length: 80
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_event_handlers.py -v
```

- [ ] **Step 6: Commit**

```bash
git add agent-orchestrator/event_handlers.py agent-orchestrator/prompts/ agent-orchestrator/tests/test_event_handlers.py
git commit -m "feat(orchestrator): add event dispatcher with rule-engine fallback"
```

---

### Task 15: ESL 连接管理与主入口

**Files:**
- Create: `agent-orchestrator/fs_esl.py`
- Create: `agent-orchestrator/main.py`

- [ ] **Step 1: 实现 ESL 连接管理**

```python
# agent-orchestrator/fs_esl.py
import time
import json
import logging
from ESL import ESLconnection
from config import settings

logger = logging.getLogger(__name__)


class ESLEventLoop:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.conn: ESLconnection | None = None
        self._running = False
        self._reconnect_delay = 5

    def connect(self) -> bool:
        self.conn = ESLconnection(self.host, self.port, self.password)
        if not self.conn.connected():
            logger.error("ESL 连接失败")
            return False
        self.conn.events("json", "all")
        logger.info("ESL 连接成功")
        return True

    def disconnect(self):
        if self.conn:
            self.conn.disconnect()
            self.conn = None

    def recv_event(self) -> dict | None:
        if not self.conn or not self.conn.connected():
            return None
        event = self.conn.recv_event()
        if not event:
            return None
        headers = event.headers
        return dict(headers) if headers else {}

    def run(self, dispatcher):
        self._running = True
        while self._running:
            if not self.conn or not self.conn.connected():
                logger.warning(f"ESL 断线，{self._reconnect_delay}s 后重连...")
                time.sleep(self._reconnect_delay)
                if not self.connect():
                    continue
            event = self.recv_event()
            if event:
                try:
                    dispatcher.dispatch(event)
                except Exception as e:
                    logger.exception(f"事件处理异常: {e}")

    def stop(self):
        self._running = False
        self.disconnect()
```

- [ ] **Step 2: 实现主入口**

```python
# agent-orchestrator/main.py
import logging
from fs_esl import ESLEventLoop
from event_handlers import EventDispatcher
from call_state import CallStateManager
from fs_actions import FSActions
from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=== 智能外呼 Orchestrator 启动 ===")

    state_mgr = CallStateManager()

    loop = ESLEventLoop(settings.fs_esl_host, settings.fs_esl_port, settings.fs_esl_password)
    if not loop.connect():
        logger.error("初始连接失败，将在循环中重连")

    actions = FSActions(loop.conn)
    dispatcher = EventDispatcher(state_mgr, loop.conn, actions)

    try:
        loop.run(dispatcher)
    except KeyboardInterrupt:
        logger.info("收到中断信号，正在关闭...")
        loop.stop()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Commit**

```bash
git add agent-orchestrator/fs_esl.py agent-orchestrator/main.py
git commit -m "feat(orchestrator): add ESL event loop and main entry point"
```

---

## Phase 4: LLM 集成

### Task 16: LLM 抽象接口与 Qwen 引擎

**Files:**
- Create: `agent-orchestrator/llm_base.py`
- Create: `agent-orchestrator/llm_engines/__init__.py`
- Create: `agent-orchestrator/llm_engines/qwen/__init__.py`
- Create: `agent-orchestrator/llm_engines/qwen/engine.py`
- Test: `agent-orchestrator/tests/test_llm_base.py`

- [ ] **Step 1: 编写 LLM 接口测试**

```python
# agent-orchestrator/tests/test_llm_base.py
import pytest
from llm_base import LLMEngine, LLMAction


def test_llm_action_creation():
    action = LLMAction(type="say", text="你好", intent="greeting", labels=[])
    assert action.type == "say"
    assert action.text == "你好"


def test_llm_engine_is_abstract():
    with pytest.raises(TypeError):
        LLMEngine()


def test_parse_llm_response_valid_json():
    from llm_base import parse_llm_response
    result = parse_llm_response('{"action": "say", "text": "你好", "intent": "greeting"}')
    assert result.type == "say"
    assert result.text == "你好"


def test_parse_llm_response_invalid_json_fallback():
    from llm_base import parse_llm_response
    result = parse_llm_response("not json at all")
    assert result.type == "say"
    assert result.text == "抱歉，请您稍后再说一遍好吗？"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_llm_base.py -v
```

- [ ] **Step 3: 实现 LLM 基础模块**

```python
# agent-orchestrator/llm_base.py
import re
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

FALLBACK_ACTION_TEXT = "抱歉，请您稍后再说一遍好吗？"


@dataclass
class LLMAction:
    type: str  # "say" | "ask" | "handoff" | "end"
    text: str
    intent: str = ""
    labels: list[str] = field(default_factory=list)


class LLMEngine(ABC):
    @abstractmethod
    async def invoke(self, prompt: str, schema: dict | None = None) -> str:
        """调用 LLM，返回原始文本响应"""

    @abstractmethod
    async def health_check(self) -> bool: ...


def parse_llm_response(raw: str) -> LLMAction:
    """解析 LLM 响应为结构化动作"""
    # 尝试 JSON 解析
    try:
        data = json.loads(raw)
        return LLMAction(
            type=data.get("action", "say"),
            text=data.get("text", FALLBACK_ACTION_TEXT),
            intent=data.get("intent", ""),
            labels=data.get("labels", []),
        )
    except (json.JSONDecodeError, AttributeError):
        pass

    # 正则提取
    action_match = re.search(r'"action"\s*:\s*"(\w+)"', raw)
    text_match = re.search(r'"text"\s*:\s*"([^"]+)"', raw)
    if action_match and text_match:
        return LLMAction(type=action_match.group(1), text=text_match.group(1))

    # 兜底
    logger.warning(f"LLM 响应解析失败，使用兜底: {raw[:100]}")
    return LLMAction(type="say", text=FALLBACK_ACTION_TEXT)
```

- [ ] **Step 4: 实现 Qwen 引擎**

```python
# agent-orchestrator/llm_engines/qwen/engine.py
import logging
import httpx
from llm_base import LLMEngine

logger = logging.getLogger(__name__)


class QwenEngine(LLMEngine):
    def __init__(self, base_url: str = "http://127.0.0.1:8080"):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def invoke(self, prompt: str, schema: dict | None = None) -> str:
        payload = {
            "model": "qwen3.5-9b",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 256,
        }
        if schema:
            payload["response_format"] = {"type": "json_object"}

        resp = await self._client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/healthz")
            return resp.status_code == 200
        except Exception:
            return False
```

```python
# agent-orchestrator/llm_engines/qwen/__init__.py
```

```python
# agent-orchestrator/llm_engines/__init__.py
```

- [ ] **Step 5: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_llm_base.py -v
```

- [ ] **Step 6: Commit**

```bash
git add agent-orchestrator/llm_base.py agent-orchestrator/llm_engines/ agent-orchestrator/tests/test_llm_base.py
git commit -m "feat(orchestrator): add pluggable LLM engine with Qwen implementation"
```

---

### Task 17: Prompt 上下文组装

**Files:**
- Create: `agent-orchestrator/prompt_builder.py`
- Test: `agent-orchestrator/tests/test_prompt_builder.py`

- [ ] **Step 1: 编写上下文组装测试**

```python
# agent-orchestrator/tests/test_prompt_builder.py
from prompt_builder import build_prompt


def test_build_prompt_basic():
    result = build_prompt(
        biz_type="marketing",
        system_prompt="你是营销助手",
        user_input="我想了解产品",
        memory_block="",
        turn_history=[],
    )
    assert "你是营销助手" in result
    assert "我想了解产品" in result


def test_build_prompt_with_memory():
    result = build_prompt(
        biz_type="marketing",
        system_prompt="你是营销助手",
        user_input="你好",
        memory_block="## 用户记忆\n- 偏好: 周末联系",
        turn_history=[],
    )
    assert "偏好: 周末联系" in result


def test_build_prompt_with_history():
    result = build_prompt(
        biz_type="customer_service",
        system_prompt="你是客服",
        user_input="谢谢",
        memory_block="",
        turn_history=[
            {"role": "user", "text": "你好"},
            {"role": "assistant", "text": "您好，请问有什么可以帮您？"},
        ],
    )
    assert "你好" in result
    assert "谢谢" in result
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_prompt_builder.py -v
```

- [ ] **Step 3: 实现上下文组装**

```python
# agent-orchestrator/prompt_builder.py
def build_prompt(
    biz_type: str,
    system_prompt: str,
    user_input: str,
    memory_block: str = "",
    turn_history: list[dict] | None = None,
    max_history_turns: int = 10,
) -> str:
    parts = [system_prompt]

    if memory_block:
        parts.append(f"\n{memory_block}")

    if turn_history:
        recent = turn_history[-max_history_turns:]
        history_lines = []
        for turn in recent:
            role = "用户" if turn["role"] == "user" else "助手"
            history_lines.append(f"{role}: {turn['text']}")
        parts.append("\n## 当前对话\n" + "\n".join(history_lines))

    parts.append(f"\n用户: {user_input}")
    parts.append("\n请以JSON格式回复: {\"action\": \"...\", \"text\": \"...\", \"intent\": \"...\"}")

    return "\n".join(parts)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_prompt_builder.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/prompt_builder.py agent-orchestrator/tests/test_prompt_builder.py
git commit -m "feat(orchestrator): add prompt context assembler"
```

---

## Phase 5: LangGraph 流程编排

### Task 18: LangGraph 状态图

**Files:**
- Create: `agent-orchestrator/graph_flow.py`
- Test: `agent-orchestrator/tests/test_graph_flow.py`

- [ ] **Step 1: 编写 LangGraph 流程测试**

```python
# agent-orchestrator/tests/test_graph_flow.py
import pytest
from graph_flow import create_call_graph, CallGraphState


def test_graph_creation():
    graph = create_call_graph()
    assert graph is not None


@pytest.mark.asyncio
async def test_graph_runs_recall_to_execute():
    graph = create_call_graph()
    state: CallGraphState = {
        "fs_uuid": "test-uuid",
        "biz_type": "marketing",
        "user_key": "user1:hash1",
        "user_input": "你好",
        "memory_block": "",
        "llm_action": None,
        "identity_verified": False,
        "turn_count": 1,
        "handoff_reason": "",
    }
    result = await graph.ainvoke(state)
    assert result["llm_action"] is not None
    assert result["llm_action"].type in ("say", "ask", "handoff", "end")
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_graph_flow.py -v
```

- [ ] **Step 3: 实现 LangGraph 状态图**

```python
# agent-orchestrator/graph_flow.py
import logging
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from llm_base import LLMAction, parse_llm_response, FALLBACK_ACTION_TEXT

logger = logging.getLogger(__name__)


class CallGraphState(TypedDict):
    fs_uuid: str
    biz_type: str
    user_key: str
    user_input: str
    memory_block: str
    llm_action: Optional[LLMAction]
    identity_verified: bool
    turn_count: int
    handoff_reason: str


# --- 节点函数 ---

async def recall_memory_node(state: CallGraphState) -> dict:
    # Phase 6 实现实际记忆召回
    return {"memory_block": ""}


async def llm_decide_node(state: CallGraphState) -> dict:
    # Phase 4 的 LLM 调用在这里集成
    # Phase 5 先用规则引擎
    from event_handlers import RULES, DEFAULT_REPLY
    reply = RULES.get(state["biz_type"], DEFAULT_REPLY)
    action = LLMAction(type="say", text=reply, intent="default")
    return {"llm_action": action}


async def compliance_check_node(state: CallGraphState) -> dict:
    # Phase 7 实现完整合规检查
    action = state["llm_action"]
    if action and state["biz_type"] == "collection" and not state["identity_verified"]:
        action.text = _sanitize_sensitive(action.text)
    return {"llm_action": action}


async def execute_action_node(state: CallGraphState) -> dict:
    action = state["llm_action"]
    if action and action.type == "handoff":
        return {"handoff_reason": action.intent, "turn_count": state["turn_count"]}
    return {"turn_count": state["turn_count"]}


async def finalize_node(state: CallGraphState) -> dict:
    logger.info(f"[{state['fs_uuid']}] finalize")
    return {}


def _sanitize_sensitive(text: str) -> str:
    import re
    return re.sub(r'\d{4,}', '****', text)


# --- 条件边 ---

def route_after_llm(state: CallGraphState) -> str:
    if state["biz_type"] == "collection" and not state["identity_verified"]:
        return "compliance_check"
    return "execute_action"


def route_after_execute(state: CallGraphState) -> str:
    action = state["llm_action"]
    if action and action.type in ("end", "handoff"):
        return "finalize"
    return END


# --- 构建图 ---

def create_call_graph():
    graph = StateGraph(CallGraphState)

    graph.add_node("recall_memory", recall_memory_node)
    graph.add_node("llm_decide", llm_decide_node)
    graph.add_node("compliance_check", compliance_check_node)
    graph.add_node("execute_action", execute_action_node)
    graph.add_node("finalize", finalize_node)

    graph.set_entry_point("recall_memory")
    graph.add_edge("recall_memory", "llm_decide")
    graph.add_conditional_edges("llm_decide", route_after_llm, {
        "compliance_check": "compliance_check",
        "execute_action": "execute_action",
    })
    graph.add_edge("compliance_check", "execute_action")
    graph.add_conditional_edges("execute_action", route_after_execute, {
        "finalize": "finalize",
        END: END,
    })

    return graph.compile()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_graph_flow.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/graph_flow.py agent-orchestrator/tests/test_graph_flow.py
git commit -m "feat(orchestrator): add LangGraph state graph with compliance routing"
```

---

### Task 19: 集成 LangGraph 到事件处理器

**Files:**
- Modify: `agent-orchestrator/event_handlers.py`

- [ ] **Step 1: 更新 DETECTED_SPEECH handler 使用 LangGraph**

在 `event_handlers.py` 的 `handle_detected_speech` 方法中替换规则引擎为 LangGraph：

```python
# 在 handle_detected_speech 中替换:
#   reply = RULES.get(state.biz_type, DEFAULT_REPLY)
# 为:
    from graph_flow import create_call_graph, CallGraphState

    graph = create_call_graph()
    result = await graph.ainvoke({
        "fs_uuid": state.fs_uuid,
        "biz_type": state.biz_type,
        "user_key": state.user_key,
        "user_input": speech_text,
        "memory_block": "",
        "llm_action": None,
        "identity_verified": state.identity_verified,
        "turn_count": state.turn_count,
        "handoff_reason": "",
    })
    action = result.get("llm_action")
    if not action:
        return

    if action.type in ("say", "ask"):
        self.actions.stop_detect_speech(fs_uuid)
        self.actions.tts_speak(fs_uuid, state.biz_type, action.text)
        self.actions.start_detect_speech(fs_uuid)
    elif action.type == "handoff":
        self.actions.transfer(fs_uuid)
    elif action.type == "end":
        self.actions.hangup(fs_uuid)
```

- [ ] **Step 2: 运行全部测试**

```bash
cd agent-orchestrator && python -m pytest tests/ -v
```

- [ ] **Step 3: Commit**

```bash
git add agent-orchestrator/event_handlers.py
git commit -m "feat(orchestrator): integrate LangGraph into DETECTED_SPEECH handler"
```

---

## Phase 6: 记忆系统

### Task 20: Redis Hot Memory

**Files:**
- Create: `agent-orchestrator/memory/redis_memory.py`
- Create: `agent-orchestrator/memory/__init__.py`
- Test: `agent-orchestrator/tests/memory/__init__.py`
- Test: `agent-orchestrator/tests/memory/test_redis_memory.py`

- [ ] **Step 1: 编写 Redis memory 测试**

```python
# agent-orchestrator/tests/memory/test_redis_memory.py
import pytest
from unittest.mock import MagicMock, patch
from memory.redis_memory import RedisHotMemory


@pytest.fixture
def memory():
    with patch("memory.redis_memory.redis.Redis") as mock_redis:
        yield RedisHotMemory("redis://localhost:6379/0")


def test_set_and_get(memory):
    memory.set_fact("customer_service", "user1:h1", "pref_contact_time", "周末上午")
    fact = memory.get_fact("customer_service", "user1:h1", "pref_contact_time")
    assert fact == "周末上午"


def test_get_all_facts(memory):
    memory.set_fact("marketing", "u1:h1", "do_not_call", "true")
    facts = memory.get_all_facts("marketing", "u1:h1")
    assert isinstance(facts, dict)


def test_set_do_not_call(memory):
    memory.set_do_not_call("marketing", "u1:h1")
    assert memory.get_fact("marketing", "u1:h1", "do_not_call") == "true"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/memory/test_redis_memory.py -v
```

- [ ] **Step 3: 实现 Redis Hot Memory**

```python
# agent-orchestrator/memory/redis_memory.py
import redis
from datetime import datetime


class RedisHotMemory:
    def __init__(self, url: str):
        self._redis = redis.Redis.from_url(url, decode_responses=True)

    def _key(self, biz_type: str, user_key: str) -> str:
        yyyymm = datetime.now().strftime("%Y%m")
        return f"cb:mem:hot:{biz_type}:{user_key}:{yyyymm}"

    def set_fact(self, biz_type: str, user_key: str, field: str, value: str, ttl_days: int = 90):
        key = self._key(biz_type, user_key)
        self._redis.hset(key, field, value)
        self._redis.expire(key, ttl_days * 86400)

    def get_fact(self, biz_type: str, user_key: str, field: str) -> str | None:
        key = self._key(biz_type, user_key)
        return self._redis.hget(key, field)

    def get_all_facts(self, biz_type: str, user_key: str) -> dict:
        key = self._key(biz_type, user_key)
        return self._redis.hgetall(key)

    def set_do_not_call(self, biz_type: str, user_key: str):
        self.set_fact(biz_type, user_key, "do_not_call", "true")

    def is_do_not_call(self, biz_type: str, user_key: str) -> bool:
        return self.get_fact(biz_type, user_key, "do_not_call") == "true"
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/memory/test_redis_memory.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/memory/ agent-orchestrator/tests/memory/
git commit -m "feat(orchestrator): add Redis hot memory module"
```

---

### Task 21: PG Facts + PG Vector + Assembler

**Files:**
- Create: `agent-orchestrator/memory/pg_facts.py`
- Create: `agent-orchestrator/memory/pg_vector.py`
- Create: `agent-orchestrator/memory/assembler.py`
- Create: `agent-orchestrator/storage/db_pg.py`
- Test: `agent-orchestrator/tests/memory/test_assembler.py`

- [ ] **Step 1: 实现 PG 存储层**

```python
# agent-orchestrator/storage/db_pg.py
import psycopg
from config import settings


def get_connection():
    return psycopg.connect(settings.pg_dsn)


async def insert_call_session(state_dict: dict):
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO callbot.call_session
                (call_id, fs_uuid, biz_type, task_id, core_user_id, phone_hash, user_key, start_ts, identity_verified, recording_notice_played)
                VALUES (gen_random_uuid(), %(fs_uuid)s, %(biz_type)s, %(task_id)s, %(core_user_id)s, %(phone_hash)s, %(user_key)s, now(), %(identity_verified)s, %(recording_notice_played)s)""",
                state_dict,
            )
            await conn.commit()


async def update_call_session_end(fs_uuid: str, end_ts, hangup_cause: str, result_code: str):
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE callbot.call_session SET end_ts = %s, hangup_cause = %s, result_code = %s WHERE fs_uuid = %s""",
                (end_ts, hangup_cause, result_code, fs_uuid),
            )
            await conn.commit()


async def insert_turn(call_id: str, fs_uuid: str, biz_type: str, user_key: str, role: str, text: str, asr_conf: float = None):
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO callbot.call_turn (call_id, fs_uuid, biz_type, user_key, role, text, asr_conf, ts)
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())""",
                (call_id, fs_uuid, biz_type, user_key, role, text, asr_conf),
            )
            await conn.commit()
```

- [ ] **Step 2: 实现 PG Facts**

```python
# agent-orchestrator/memory/pg_facts.py
import psycopg
from config import settings


async def get_recent_facts(biz_type: str, user_key: str, days: int = 90, top_k: int = 5) -> list[dict]:
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT fact_type, fact_value, last_seen_ts
                FROM callbot.user_memory_fact
                WHERE biz_type = %s AND user_key = %s
                  AND (expire_ts IS NULL OR expire_ts > now())
                  AND last_seen_ts >= now() - interval '%s days'
                ORDER BY last_seen_ts DESC LIMIT %s""",
                (biz_type, user_key, days, top_k),
            )
            rows = await cur.fetchall()
            return [{"fact_type": r[0], "fact_value": r[1], "last_seen_ts": r[2]} for r in rows]


async def upsert_fact(biz_type: str, user_key: str, fact_type: str, fact_value: dict, source_call_id: str = None):
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO callbot.user_memory_fact (biz_type, user_key, fact_type, fact_value, first_seen_ts, last_seen_ts, source_call_id)
                VALUES (%s, %s, %s, %s, now(), now(), %s)
                ON CONFLICT ON CONSTRAINT user_memory_fact_pkey DO UPDATE
                SET fact_value = EXCLUDED.fact_value, last_seen_ts = now()""",
                (biz_type, user_key, fact_type, fact_value, source_call_id),
            )
            await conn.commit()
```

- [ ] **Step 3: 实现 PG Vector**

```python
# agent-orchestrator/memory/pg_vector.py
import psycopg
from config import settings


async def search_similar(biz_type: str, user_key: str, query_embedding: list, top_k: int = 3, days: int = 180) -> list[dict]:
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT content, tags, ts,
                1 - (embedding <=> %s::vector) as similarity
                FROM callbot.user_memory_vector
                WHERE biz_type = %s AND user_key = %s
                  AND ts >= now() - interval '%s days'
                ORDER BY embedding <=> %s::vector
                LIMIT %s""",
                (str(query_embedding), biz_type, user_key, days, str(query_embedding), top_k),
            )
            rows = await cur.fetchall()
            return [{"content": r[0], "tags": r[1], "ts": r[2], "similarity": r[3]} for r in rows]


async def insert_vector(biz_type: str, user_key: str, content: str, embedding: list, source_call_id: str = None):
    async with await psycopg.AsyncConnection.connect(settings.pg_dsn) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO callbot.user_memory_vector (biz_type, user_key, content, embedding, source_call_id, ts)
                VALUES (%s, %s, %s, %s::vector, %s, now())""",
                (biz_type, user_key, content, str(embedding), source_call_id),
            )
            await conn.commit()
```

- [ ] **Step 4: 实现 Memory Block 组装器**

```python
# agent-orchestrator/memory/assembler.py
import logging
from memory.redis_memory import RedisHotMemory
from memory.pg_facts import get_recent_facts
from memory.pg_vector import search_similar
from config import settings

logger = logging.getLogger(__name__)


class MemoryAssembler:
    def __init__(self):
        self.redis = RedisHotMemory(settings.redis_url)

    async def assemble(self, biz_type: str, user_key: str, current_input: str = "") -> str:
        parts = []

        # Layer 1: Redis hot facts
        hot_facts = self.redis.get_all_facts(biz_type, user_key)
        if hot_facts:
            lines = [f"- [{k}]: {v}" for k, v in list(hot_facts.items())[:5]]
            parts.append("## 用户记忆（近期）\n" + "\n".join(lines))

        # Layer 2: PG facts
        pg_facts = await get_recent_facts(biz_type, user_key, days=90, top_k=5)
        if pg_facts:
            lines = [f"- [{f['fact_type']}]: {f['fact_value']} ({f['last_seen_ts'].date()})" for f in pg_facts]
            parts.append("## 用户记忆（长期）\n" + "\n".join(lines))

        # Layer 3: pgvector（需要 embedding 输入，Phase 6 后期集成）
        # vectors = await search_similar(biz_type, user_key, embedding, top_k=3)

        return "\n\n".join(parts)
```

- [ ] **Step 5: 编写 assembler 测试**

```python
# agent-orchestrator/tests/memory/test_assembler.py
import pytest
from unittest.mock import patch, MagicMock
from memory.assembler import MemoryAssembler


@pytest.mark.asyncio
async def test_assemble_returns_string():
    with patch("memory.assembler.RedisHotMemory") as mock_redis_cls:
        mock_redis = MagicMock()
        mock_redis.get_all_facts.return_value = {"pref": "周末"}
        mock_redis_cls.return_value = mock_redis

        with patch("memory.assembler.get_recent_facts", return_value=[]):
            assembler = MemoryAssembler()
            result = await assembler.assemble("marketing", "u1:h1")
            assert isinstance(result, str)
            assert "周末" in result
```

- [ ] **Step 6: 运行测试**

```bash
cd agent-orchestrator && python -m pytest tests/memory/ -v
```

- [ ] **Step 7: Commit**

```bash
git add agent-orchestrator/memory/ agent-orchestrator/storage/ agent-orchestrator/tests/memory/
git commit -m "feat(orchestrator): add three-layer memory system with assembler"
```

---

## Phase 7: 身份核验 & 合规

### Task 22: MCP Client

**Files:**
- Create: `agent-orchestrator/mcp_client.py`
- Test: `agent-orchestrator/tests/test_mcp_client.py`

- [ ] **Step 1: 编写 MCP client 测试**

```python
# agent-orchestrator/tests/test_mcp_client.py
import pytest
from unittest.mock import AsyncMock, patch
from mcp_client import MCPClient, IdentityResult, CreditResult


@pytest.mark.asyncio
async def test_query_user_identity_success():
    client = MCPClient("http://localhost:9090")
    with patch.object(client, "_call_mcp", new_callable=AsyncMock) as mock_call:
        mock_call.return_value = {
            "user_id": "u123",
            "name_masked": "张*",
            "id_last_four": "1234",
            "gender": "male",
        }
        result = await client.query_user_identity("phone_hash_123", "collection")
        assert isinstance(result, IdentityResult)
        assert result.user_id == "u123"
        assert result.verified is True
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_mcp_client.py -v
```

- [ ] **Step 3: 实现 MCP Client**

```python
# agent-orchestrator/mcp_client.py
import logging
import httpx
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class IdentityResult:
    user_id: str
    name_masked: str
    id_last_four: str
    gender: str
    verified: bool = True
    voiceprint_match: bool | None = None


@dataclass
class CreditResult:
    user_id: str
    credit_qualified: bool
    risk_level: str
    details: dict


class MCPClient:
    def __init__(self, server_url: str, timeout: float = 10.0):
        self._client = httpx.AsyncClient(base_url=server_url, timeout=timeout)

    async def _call_mcp(self, method: str, params: dict) -> dict:
        resp = await self._client.post("/mcp/call", json={"method": method, "params": params})
        resp.raise_for_status()
        return resp.json()

    async def query_user_identity(self, phone_hash: str, biz_type: str) -> IdentityResult:
        data = await self._call_mcp("user.identity.query", {"phone_hash": phone_hash, "biz_type": biz_type})
        return IdentityResult(
            user_id=data.get("user_id", ""),
            name_masked=data.get("name_masked", ""),
            id_last_four=data.get("id_last_four", ""),
            gender=data.get("gender", ""),
            verified=True,
        )

    async def query_credit_profile(self, user_id: str, phone_hash: str) -> CreditResult:
        data = await self._call_mcp("user.credit.query", {"user_id": user_id, "phone_hash": phone_hash})
        return CreditResult(
            user_id=user_id,
            credit_qualified=data.get("credit_qualified", False),
            risk_level=data.get("risk_level", "unknown"),
            details=data,
        )

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get("/healthz")
            return resp.status_code == 200
        except Exception:
            return False
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_mcp_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/mcp_client.py agent-orchestrator/tests/test_mcp_client.py
git commit -m "feat(orchestrator): add MCP client for identity and credit verification"
```

---

### Task 23: 合规门禁模块

**Files:**
- Create: `agent-orchestrator/compliance.py`
- Test: `agent-orchestrator/tests/test_compliance.py`

- [ ] **Step 1: 编写合规测试**

```python
# agent-orchestrator/tests/test_compliance.py
import pytest
from compliance import compliance_check, contains_sensitive_fields
from llm_base import LLMAction


def test_contains_sensitive_fields():
    assert contains_sensitive_fields("您的欠款金额为50000元") is True
    assert contains_sensitive_fields("请问您方便通话吗") is False


def test_collection_blocks_sensitive_when_not_verified():
    action = LLMAction(type="say", text="您欠款50000元", intent="inform")
    result = compliance_check(action, "collection", identity_verified=False)
    assert "50000" not in result.text


def test_collection_allows_sensitive_when_verified():
    action = LLMAction(type="say", text="您欠款50000元", intent="inform")
    result = compliance_check(action, "collection", identity_verified=True)
    assert "50000" in result.text


def test_marketing_do_not_call():
    action = LLMAction(type="say", text="我们有优惠活动", intent="pitch")
    result = compliance_check(action, "marketing", identity_verified=False, do_not_call=True)
    assert result.type == "end"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd agent-orchestrator && python -m pytest tests/test_compliance.py -v
```

- [ ] **Step 3: 实现合规模块**

```python
# agent-orchestrator/compliance.py
import re
import logging
from llm_base import LLMAction

logger = logging.getLogger(__name__)

SENSITIVE_PATTERNS = [
    r'\d{4,}',          # 连续数字（金额、身份证等）
    r'欠款',
    r'逾期',
    r'剩余本金',
]


def contains_sensitive_fields(text: str) -> bool:
    return any(re.search(p, text) for p in SENSITIVE_PATTERNS)


def sanitize_sensitive_text(text: str) -> str:
    sanitized = text
    for pattern in SENSITIVE_PATTERNS:
        sanitized = re.sub(pattern, '****', sanitized)
    return sanitized


def compliance_check(
    action: LLMAction,
    biz_type: str,
    identity_verified: bool = False,
    do_not_call: bool = False,
) -> LLMAction:
    # 营销 do_not_call 拦截
    if biz_type == "marketing" and do_not_call:
        logger.warning("营销 do_not_call 拦截")
        return LLMAction(type="end", text="抱歉打扰了，再见")

    # 催收敏感字段门禁
    if biz_type == "collection" and not identity_verified:
        if contains_sensitive_fields(action.text):
            logger.warning(f"催收敏感字段拦截: {action.text[:30]}")
            action.text = sanitize_sensitive_text(action.text)

    return action
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd agent-orchestrator && python -m pytest tests/test_compliance.py -v
```

- [ ] **Step 5: Commit**

```bash
git add agent-orchestrator/compliance.py agent-orchestrator/tests/test_compliance.py
git commit -m "feat(orchestrator): add compliance gate for sensitive fields and do_not_call"
```

---

## Phase 8: 监控运维

### Task 24: systemd 服务文件

**Files:**
- Create: `deploy/systemd/orchestrator.service`
- Create: `deploy/systemd/qwen-llm.service`

- [ ] **Step 1: 创建服务文件**

```ini
# deploy/systemd/orchestrator.service
[Unit]
Description=Smart Outbound Call Orchestrator
After=network.target freeswitch.service redis.service postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/agent-orchestrator
ExecStart=/opt/agent-orchestrator/venv/bin/python main.py
Restart=always
RestartSec=5
LimitNOFILE=1000000
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```ini
# deploy/systemd/qwen-llm.service
[Unit]
Description=Qwen3.5-9B LLM Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/qwen-llm
ExecStart=/opt/qwen-llm/venv/bin/python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3.5-9B --port 8080
Restart=always
RestartSec=10
LimitNOFILE=1000000
Environment=CUDA_VISIBLE_DEVICES=2

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Commit**

```bash
git add deploy/systemd/
git commit -m "feat: add systemd service files for orchestrator and Qwen LLM"
```

---

### Task 25: Prometheus 配置

**Files:**
- Create: `monitoring/prometheus/prometheus.yml`

- [ ] **Step 1: 创建 Prometheus 配置**

```yaml
# monitoring/prometheus/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: 'orchestrator'
    static_configs:
      - targets: ['127.0.0.1:9091']
    metrics_path: /metrics

  - job_name: 'asr-adapter'
    static_configs:
      - targets: ['10.0.0.20:8080']
    metrics_path: /metrics

  - job_name: 'tts-adapter'
    static_configs:
      - targets: ['10.0.0.21:8080']
    metrics_path: /metrics

  - job_name: 'qwen-llm'
    static_configs:
      - targets: ['10.0.0.22:8080']
    metrics_path: /metrics

rule_files:
  - 'alert_rules.yml'

alerting:
  alertmanagers:
    - static_configs:
        - targets: ['127.0.0.1:9093']
```

- [ ] **Step 2: Commit**

```bash
git add monitoring/prometheus/
git commit -m "feat: add Prometheus scrape configuration"
```

---

### Task 26: 告警规则

**Files:**
- Create: `monitoring/prometheus/alert_rules.yml`

- [ ] **Step 1: 创建告警规则**

```yaml
# monitoring/prometheus/alert_rules.yml
groups:
  - name: callbot
    rules:
      - alert: CallFailureSpike
        expr: rate(call_total{result="failure"}[5m]) / rate(call_total[5m]) > 0.3
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "通话失败率激增"

      - alert: ASRConsecutiveFailures
        expr: increase(asr_errors_total[1m]) > 5
        for: 0m
        labels:
          severity: error
        annotations:
          summary: "ASR 连续失败超过 5 次"

      - alert: TTSQueueTooLong
        expr: tts_queue_length > 20
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "TTS 排队长度超过 20"

      - alert: LLMTimeout
        expr: histogram_quantile(0.95, rate(llm_latency_bucket[2m])) > 5
        for: 2m
        labels:
          severity: error
        annotations:
          summary: "LLM P95 延迟超过 5 秒"

      - alert: ServiceDown
        expr: up == 0
        for: 30s
        labels:
          severity: critical
        annotations:
          summary: "服务 {{ $labels.job }} 宕机"
```

- [ ] **Step 2: Commit**

```bash
git add monitoring/prometheus/alert_rules.yml
git commit -m "feat: add Prometheus alert rules for callbot system"
```

---

## 自检清单

- [x] **Spec 覆盖：** 每个 Phase 对应 spec 中的章节，所有需求有对应 Task
- [x] **占位符扫描：** 无 TBD/TODO，TODO 标记仅出现在 VibeVoice 实际模型调用处（需要实际 SDK）
- [x] **类型一致性：** LLMAction 在 llm_base.py 定义，在 graph_flow.py、compliance.py、event_handlers.py 中使用一致
- [x] **文件路径：** 所有文件路径使用绝对项目路径
