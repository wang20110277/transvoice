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
-- 审计设计原则
-- ========================================
-- 所有业务表必须包含三个审计维度：
--   user_id   — 用户维度（来自用户中心，用于跨通话追溯）
--   biz_type  — 业务维度（客服/催收/营销，用于业务隔离审计）
--   call_id   — 通话维度（唯一标识，用于单通通话完整追溯）
-- 三个维度组合索引覆盖以下审计场景：
--   1. 按 user_id 查某用户所有通话记录（用户维度审计）
--   2. 按 biz_type + 时间范围 查某业务线通话量（业务维度统计）
--   3. 按 call_id 查某通通话的完整数据（全链路追溯）
--   4. 按 user_id + biz_type 查某用户在某业务线的记录（交叉审计）
--
-- 分库分表策略（应用层路由，PostgreSQL 无原生分库）：
--   分库：4 个 PostgreSQL 数据库 callbot_0~callbot_3，按 hash(user_id) % 4 路由到库
--   分表：每库内 PARTITION BY HASH(user_id)，按 hash(user_id) % N 路由到分片
--   优势：用户维度查询天然路由到单库单分片，便于后续同步数仓按用户维度拆分
--   config_snapshot 数据量小不做分库分表，放 callbot_0
--
--   测试环境：1 库 + MODULUS 4（4分片，p0~p3）
--   生产环境：4 库 + 每库 MODULUS 128（128分片，p0~p127）
--   以下 DDL 以测试环境为例，生产部署时需：
--     1. 创建 callbot_0~callbot_3 四个数据库
--     2. 每库执行建表语句（MODULUS 改为 128，REMAINDER 0~127）
--     3. 应用层路由逻辑：db_index = hash(user_id) % 4
-- ========================================

-- ========================================
-- 2. 通话会话表（事实主表，按 user_id HASH 分表）
--    记录每通通话的完整生命周期
--    按 user_id HASH 4分片，用户维度查询天然路由到单分片
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_session (
  id                 BIGSERIAL,                  -- 主键自增 ID
  call_id            UUID NOT NULL,           -- 通话业务标识（系统生成，供子表关联）
  fs_uuid            UUID NOT NULL,           -- FreeSWITCH 会话唯一标识
  biz_type           TEXT NOT NULL CHECK (biz_type IN ('customer_service','collection','marketing')),  -- 业务类型：客服/催收/营销
  task_id            TEXT,                     -- 外呼任务 ID（来自任务调度系统）
  user_id            TEXT NOT NULL,           -- 用户 ID（分表键 + 审计主维度）
  phone_hash         TEXT NOT NULL,           -- 手机号加盐哈希（不存明文）
  user_key           TEXT NOT NULL,           -- 复合用户标识：user_id:phone_hash（业务查询用）
  phone_masked       TEXT,                     -- 脱敏手机号（如 138****1234）
  start_ts           TIMESTAMPTZ NOT NULL,    -- 通话开始时间
  end_ts             TIMESTAMPTZ,             -- 通话结束时间（挂断时更新）
  result_code        TEXT,                     -- 通话结果编码（normal_end/user_busy/no_answer 等）
  hangup_cause       TEXT,                     -- 挂机原因（对应 SIP Hangup-Cause）
  identity_verified  BOOLEAN NOT NULL DEFAULT FALSE,  -- 身份核验是否通过
  verify_attempts    INT NOT NULL DEFAULT 0,  -- 核验尝试次数
  recording_notice_played BOOLEAN NOT NULL DEFAULT FALSE,  -- 录音告知是否已播放（合规关键）
  create_time       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user       TEXT NOT NULL DEFAULT 'system',       -- 创建人（系统自动 / 运维人员）
  update_time       TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user       TEXT NOT NULL DEFAULT 'system',       -- 最后更新人
  PRIMARY KEY (id, user_id)
) PARTITION BY HASH (user_id);

-- 业务索引：按 call_id 唯一约束（业务关联键）
CREATE UNIQUE INDEX IF NOT EXISTS idx_call_session_call_id
  ON callbot.call_session (call_id);
-- 审计索引：按 user_id 查某用户所有通话历史
CREATE INDEX IF NOT EXISTS idx_call_session_user_start
  ON callbot.call_session (user_id, start_ts DESC);
-- 审计索引：按 user_id + biz_type 交叉查询
CREATE INDEX IF NOT EXISTS idx_call_session_user_biz_start
  ON callbot.call_session (user_id, biz_type, start_ts DESC);
-- 审计索引：按 biz_type + 时间范围 查业务线通话量
CREATE INDEX IF NOT EXISTS idx_call_session_biz_start
  ON callbot.call_session (biz_type, start_ts DESC);
-- 业务索引：按任务维度查询通话记录
CREATE INDEX IF NOT EXISTS idx_call_session_task_start
  ON callbot.call_session (biz_type, task_id, start_ts DESC);

-- HASH 分表（4分片）
CREATE TABLE IF NOT EXISTS callbot.call_session_p0
  PARTITION OF callbot.call_session FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE IF NOT EXISTS callbot.call_session_p1
  PARTITION OF callbot.call_session FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE IF NOT EXISTS callbot.call_session_p2
  PARTITION OF callbot.call_session FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE IF NOT EXISTS callbot.call_session_p3
  PARTITION OF callbot.call_session FOR VALUES WITH (MODULUS 4, REMAINDER 3);

-- ========================================
-- 3. 逐轮对话表（按 user_id HASH 分表）
--    记录每通通话中的每一轮用户/助手交互
--    数据量最大（每轮对话一条），按 user_id HASH 4分片
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_turn (
  id             BIGSERIAL,                   -- 主键自增 ID
  call_id        UUID NOT NULL,               -- 关联通话会话（审计维度：通话维度）
  fs_uuid        UUID NOT NULL,               -- FreeSWITCH 会话标识（便于排查）
  biz_type       TEXT NOT NULL,               -- 业务类型（审计维度：业务维度）
  user_id        TEXT NOT NULL,               -- 用户 ID（分表键 + 审计维度）
  user_key       TEXT NOT NULL,               -- 复合用户标识（业务查询用）
  role           TEXT NOT NULL CHECK (role IN ('user','assistant','system','tool')),  -- 角色：用户/助手/系统/工具
  text           TEXT,                         -- 对话文本内容
  asr_conf       REAL,                         -- ASR 识别置信度（0.0~1.0）
  start_ms       INT,                          -- 轮次开始毫秒偏移（相对通话开始）
  end_ms         INT,                          -- 轮次结束毫秒偏移
  ts             TIMESTAMPTZ NOT NULL,         -- 时间戳
  create_time    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user    TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user    TEXT NOT NULL DEFAULT 'system',       -- 最后更新人
  PRIMARY KEY (id, user_id)
) PARTITION BY HASH (user_id);

-- 审计索引：按 call_id 查某通通话的所有轮次（全链路追溯）
CREATE INDEX IF NOT EXISTS idx_call_turn_call
  ON callbot.call_turn (call_id, ts);
-- 审计索引：按 user_id 查某用户所有对话记录
CREATE INDEX IF NOT EXISTS idx_call_turn_user_ts
  ON callbot.call_turn (user_id, ts DESC);
-- 审计索引：按 user_id + biz_type 交叉查询
CREATE INDEX IF NOT EXISTS idx_call_turn_user_biz_ts
  ON callbot.call_turn (user_id, biz_type, ts DESC);

-- HASH 分表（4分片）
CREATE TABLE IF NOT EXISTS callbot.call_turn_p0
  PARTITION OF callbot.call_turn FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE IF NOT EXISTS callbot.call_turn_p1
  PARTITION OF callbot.call_turn FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE IF NOT EXISTS callbot.call_turn_p2
  PARTITION OF callbot.call_turn FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE IF NOT EXISTS callbot.call_turn_p3
  PARTITION OF callbot.call_turn FOR VALUES WITH (MODULUS 4, REMAINDER 3);

-- ========================================
-- 4. 事件流表（按 user_id HASH 分表）
--    记录通话生命周期中的所有事件（状态变更、告警、动作等）
--    按 user_id HASH 4分片
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_event (
  id            BIGSERIAL,                    -- 主键自增 ID
  call_id       UUID NOT NULL,                -- 关联通话会话（审计维度：通话维度）
  fs_uuid       UUID NOT NULL,                -- FreeSWITCH 会话标识
  biz_type      TEXT NOT NULL,                -- 业务类型（审计维度：业务维度）
  user_id       TEXT NOT NULL,               -- 用户 ID（分表键 + 审计维度）
  user_key      TEXT NOT NULL,               -- 复合用户标识（业务查询用）
  event_type    TEXT NOT NULL,                -- 事件类型（如 LEGAL_NOTICE_FAILED, HANDOFF, SENSITIVE_FIELD_BLOCKED）
  payload       JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 事件详情（JSON 格式，灵活扩展）
  ts            TIMESTAMPTZ NOT NULL,         -- 事件时间戳
  create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user   TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user   TEXT NOT NULL DEFAULT 'system',       -- 最后更新人
  PRIMARY KEY (id, user_id)
) PARTITION BY HASH (user_id);

-- 审计索引：按 call_id 查某通通话的事件流（全链路追溯）
CREATE INDEX IF NOT EXISTS idx_call_event_call
  ON callbot.call_event (call_id, ts);
-- 审计索引：按 user_id 查某用户所有事件
CREATE INDEX IF NOT EXISTS idx_call_event_user_ts
  ON callbot.call_event (user_id, ts DESC);
-- 审计索引：按 user_id + biz_type 交叉查询
CREATE INDEX IF NOT EXISTS idx_call_event_user_biz_ts
  ON callbot.call_event (user_id, biz_type, ts DESC);
-- 业务索引：按事件类型查询（用于告警统计）
CREATE INDEX IF NOT EXISTS idx_call_event_type_ts
  ON callbot.call_event (biz_type, event_type, ts DESC);

-- HASH 分表（4分片）
CREATE TABLE IF NOT EXISTS callbot.call_event_p0
  PARTITION OF callbot.call_event FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE IF NOT EXISTS callbot.call_event_p1
  PARTITION OF callbot.call_event FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE IF NOT EXISTS callbot.call_event_p2
  PARTITION OF callbot.call_event FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE IF NOT EXISTS callbot.call_event_p3
  PARTITION OF callbot.call_event FOR VALUES WITH (MODULUS 4, REMAINDER 3);

-- ========================================
-- 5. 录音/音频产物表（按 user_id HASH 分表）
--    记录所有录音文件和 TTS 音频的存储位置与元数据
--    每通通话产生多个音频文件，按 user_id HASH 4分片
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.call_artifact (
  id            BIGSERIAL,                   -- 主键自增 ID
  call_id       UUID NOT NULL,                -- 关联通话会话（审计维度：通话维度）
  fs_uuid       UUID NOT NULL,                -- FreeSWITCH 会话标识
  biz_type      TEXT NOT NULL,                -- 业务类型（审计维度：业务维度）
  user_id       TEXT NOT NULL,               -- 用户 ID（分表键 + 审计维度）
  user_key      TEXT NOT NULL,               -- 复合用户标识
  kind          TEXT NOT NULL,                -- 文件类型：caller_wav(主叫录音)/bot_wav(机器人录音)/mix_wav(混音)/tts_wav(TTS音频)/meta_json(元数据)
  storage       TEXT NOT NULL CHECK (storage IN ('nas','minio')),  -- 存储介质：NAS 热存/MinIO 归档
  uri           TEXT NOT NULL,                -- 文件路径或对象 key
  sha256        TEXT,                          -- 文件哈希（完整性校验）
  size_bytes    BIGINT,                        -- 文件大小（字节）
  content_type  TEXT,                          -- MIME 类型（如 audio/wav）
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 文件创建时间
  create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 记录创建时间
  create_user   TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user   TEXT NOT NULL DEFAULT 'system',       -- 最后更新人
  PRIMARY KEY (id, user_id)
) PARTITION BY HASH (user_id);

-- 审计索引：按 call_id + kind 查某通通话的所有录音文件
CREATE INDEX IF NOT EXISTS idx_artifact_call
  ON callbot.call_artifact (call_id, kind);
-- 审计索引：按 user_id 查某用户的所有录音
CREATE INDEX IF NOT EXISTS idx_artifact_user_ts
  ON callbot.call_artifact (user_id, ts DESC);
-- 审计索引：按 biz_type + 时间范围 查业务线录音量
CREATE INDEX IF NOT EXISTS idx_artifact_biz_ts
  ON callbot.call_artifact (biz_type, ts DESC);

-- HASH 分表（4分片）
CREATE TABLE IF NOT EXISTS callbot.call_artifact_p0
  PARTITION OF callbot.call_artifact FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE IF NOT EXISTS callbot.call_artifact_p1
  PARTITION OF callbot.call_artifact FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE IF NOT EXISTS callbot.call_artifact_p2
  PARTITION OF callbot.call_artifact FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE IF NOT EXISTS callbot.call_artifact_p3
  PARTITION OF callbot.call_artifact FOR VALUES WITH (MODULUS 4, REMAINDER 3);

-- ========================================
-- 6. 配置快照表
--    每通通话开始时冻结 Prompt/Flow/TTS/Dialplan 版本，确保可追溯
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.config_snapshot (
  id            BIGSERIAL PRIMARY KEY,        -- 主键自增 ID
  call_id       UUID NOT NULL,                -- 关联通话会话（审计维度：通话维度）
  fs_uuid       UUID NOT NULL,                -- FreeSWITCH 会话标识
  biz_type      TEXT NOT NULL,                -- 业务类型（审计维度：业务维度）
  user_id       TEXT NOT NULL,               -- 用户 ID（审计维度：用户维度）
  user_key      TEXT NOT NULL,               -- 复合用户标识
  prompt_version TEXT,                         -- Prompt 模板版本号
  flow_version   TEXT,                         -- LangGraph 流程版本号
  tts_profile_version TEXT,                    -- TTS 配置版本号
  dialplan_version TEXT,                       -- 拨号计划版本号
  snapshot      JSONB NOT NULL,               -- 完整配置快照（JSON 格式）
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 快照时间
  create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user   TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user   TEXT NOT NULL DEFAULT 'system'        -- 最后更新人
);

-- 审计索引：按 call_id 查某通通话使用的配置快照
CREATE INDEX IF NOT EXISTS idx_snapshot_call
  ON callbot.config_snapshot (call_id, ts DESC);
-- 审计索引：按 user_id 查某用户涉及的配置快照
CREATE INDEX IF NOT EXISTS idx_snapshot_user_ts
  ON callbot.config_snapshot (user_id, ts DESC);

-- ========================================
-- 7. 结构化记忆表（mem0 facts，按 user_id HASH 分表）
--    存储从对话中抽取的结构化事实（用户偏好、核验状态等）
--    随用户量增长数据量大，按 user_id HASH 4分片
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact (
  id            BIGSERIAL,                   -- 记忆自增 ID
  biz_type      TEXT NOT NULL,                -- 业务类型（审计维度：业务维度，记忆按业务隔离）
  user_id       TEXT NOT NULL,               -- 用户 ID（分表键 + 审计维度）
  user_key      TEXT NOT NULL,               -- 复合用户标识
  fact_type     TEXT NOT NULL,                -- 事实类型（如 do_not_call/preferred_contact_time/identity_verified）
  fact_value    JSONB NOT NULL,               -- 事实内容（JSON 格式，支持复杂结构）
  confidence    REAL,                          -- 置信度（规则抽取=1.0，LLM 抽取<1.0）
  first_seen_ts TIMESTAMPTZ NOT NULL,         -- 首次发现时间
  last_seen_ts  TIMESTAMPTZ NOT NULL,         -- 最近确认时间（每次命中时更新）
  source_call_id UUID,                         -- 来源通话 ID（审计维度：可追溯到具体通话）
  expire_ts     TIMESTAMPTZ,                  -- 过期时间（NULL=永不过期）
  create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user   TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user   TEXT NOT NULL DEFAULT 'system',       -- 最后更新人
  PRIMARY KEY (id, user_id)
) PARTITION BY HASH (user_id);

-- 审计索引：按 user_id 查某用户的所有记忆
CREATE INDEX IF NOT EXISTS idx_mem_fact_user
  ON callbot.user_memory_fact (user_id, fact_type);
-- 审计索引：按 user_id + biz_type 交叉查询
CREATE INDEX IF NOT EXISTS idx_mem_fact_user_biz
  ON callbot.user_memory_fact (user_id, biz_type);
-- 业务索引：按最近确认时间排序（用于召回最新记忆）
CREATE INDEX IF NOT EXISTS idx_mem_fact_lastseen
  ON callbot.user_memory_fact (biz_type, user_key, last_seen_ts DESC);

-- HASH 分表（4分片）
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact_p0
  PARTITION OF callbot.user_memory_fact FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact_p1
  PARTITION OF callbot.user_memory_fact FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact_p2
  PARTITION OF callbot.user_memory_fact FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE IF NOT EXISTS callbot.user_memory_fact_p3
  PARTITION OF callbot.user_memory_fact FOR VALUES WITH (MODULUS 4, REMAINDER 3);

-- ========================================
-- 8. 向量记忆表（pgvector，按 user_id HASH 分表）
--    存储对话摘要、用户异议、处理片段的向量嵌入，支持相似度召回
--    按 user_id HASH 4分片
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector (
  id            BIGSERIAL,                    -- 向量自增 ID
  biz_type      TEXT NOT NULL,                -- 业务类型（审计维度：业务维度）
  user_id       TEXT NOT NULL,               -- 用户 ID（分表键 + 审计维度）
  user_key      TEXT NOT NULL,               -- 复合用户标识
  content       TEXT NOT NULL,                -- 原始文本内容
  embedding     vector(1536) NOT NULL,        -- 向量嵌入（1536维，与 OpenAI embedding 对齐）
  tags          JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 标签（如情绪/异议类型/话术有效性）
  source_call_id UUID,                         -- 来源通话 ID（审计维度：可追溯到具体通话）
  source_turn_id BIGINT,                       -- 来源轮次 ID
  ts            TIMESTAMPTZ NOT NULL,         -- 时间戳
  create_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user   TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time   TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user   TEXT NOT NULL DEFAULT 'system',       -- 最后更新人
  PRIMARY KEY (id, user_id)
) PARTITION BY HASH (user_id);

-- 审计索引：按 user_id 查某用户的所有向量记忆
CREATE INDEX IF NOT EXISTS idx_mem_vec_user_ts
  ON callbot.user_memory_vector (user_id, ts DESC);
-- 审计索引：按 user_id + biz_type 交叉查询
CREATE INDEX IF NOT EXISTS idx_mem_vec_user_biz_ts
  ON callbot.user_memory_vector (user_id, biz_type, ts DESC);

-- HASH 分表（4分片）
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector_p0
  PARTITION OF callbot.user_memory_vector FOR VALUES WITH (MODULUS 4, REMAINDER 0);
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector_p1
  PARTITION OF callbot.user_memory_vector FOR VALUES WITH (MODULUS 4, REMAINDER 1);
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector_p2
  PARTITION OF callbot.user_memory_vector FOR VALUES WITH (MODULUS 4, REMAINDER 2);
CREATE TABLE IF NOT EXISTS callbot.user_memory_vector_p3
  PARTITION OF callbot.user_memory_vector FOR VALUES WITH (MODULUS 4, REMAINDER 3);

-- HNSW 向量索引（必须在每个分片上创建，支持高效近似最近邻检索）
CREATE INDEX IF NOT EXISTS idx_mem_vec_p0_hnsw
  ON callbot.user_memory_vector_p0 USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_mem_vec_p1_hnsw
  ON callbot.user_memory_vector_p1 USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_mem_vec_p2_hnsw
  ON callbot.user_memory_vector_p2 USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_mem_vec_p3_hnsw
  ON callbot.user_memory_vector_p3 USING hnsw (embedding vector_cosine_ops);

-- ========================================
-- 9. 话术知识库表（Agentic RAG，共享知识库不分库分表）
--    存储各业务线的话术模板、FAQ、异议处理策略，LLM 实时检索匹配
--    按业务线 + 场景分类，向量嵌入支持语义相似度召回
-- ========================================
CREATE TABLE IF NOT EXISTS callbot.script_library (
  id              BIGSERIAL PRIMARY KEY,             -- 主键自增 ID
  biz_type        TEXT NOT NULL,                     -- 业务类型（customer_service/collection/marketing）
  scene           TEXT NOT NULL,                     -- 场景标签（如 opening/probing/objection_handling/closing/compliance_notice）
  title           TEXT NOT NULL,                     -- 话术标题（如 "催收-承诺还款确认"）
  content         TEXT NOT NULL,                     -- 话术正文（完整话术内容）
  conditions      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 触发条件（如 {"intent":"promise_to_pay","turn_range":[3,8]}）
  tags            JSONB NOT NULL DEFAULT '{}'::jsonb,  -- 扩展标签（如 {"emotion":"empathy","priority":1}）
  embedding       vector(1536),                      -- 话术向量嵌入（1536维，由 embedding 服务异步生成）
  version         INT NOT NULL DEFAULT 1,            -- 话术版本号（支持灰度更新）
  is_active       BOOLEAN NOT NULL DEFAULT TRUE,     -- 是否启用（软删除用）
  create_time     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 创建时间
  create_user     TEXT NOT NULL DEFAULT 'system',       -- 创建人
  update_time     TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最后更新时间
  update_user     TEXT NOT NULL DEFAULT 'system'        -- 最后更新人
);

-- 业务索引：按 biz_type + scene 查询某业务线某场景的所有话术
CREATE INDEX IF NOT EXISTS idx_script_biz_scene
  ON callbot.script_library (biz_type, scene) WHERE is_active = TRUE;
-- 业务索引：按 biz_type + version 查询最新版本话术
CREATE INDEX IF NOT EXISTS idx_script_biz_version
  ON callbot.script_library (biz_type, version DESC) WHERE is_active = TRUE;
-- HNSW 向量索引（语义相似度召回，仅索引已生成嵌入的活跃话术）
CREATE INDEX IF NOT EXISTS idx_script_hnsw
  ON callbot.script_library USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL AND is_active = TRUE;
