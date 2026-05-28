# 项目全局审计报告

**审计日期:** 2026-05-20
**审计范围:** agent-asr, agent-tts, agent-flow, mcp-server/java-mcp-server
**审计维度:** 代码质量、架构结构、安全隐患、性能问题

---

## 统计总览

| 严重程度 | 数量 | 说明 |
|----------|------|------|
| CRITICAL | 11 | 必须立即修复 |
| HIGH | 17 | 应尽快修复 |
| MEDIUM | 16 | 建议修复 |
| LOW | 10 | 可选优化 |

---

## CRITICAL 问题（11 项）

### C-01. 所有端点无认证/鉴权
**组件:** 全部
**位置:** 所有 FastAPI 服务 + MCP 服务
**问题:** 所有 HTTP/WebSocket 端点无认证，任何人可调用。包括 ASR/TTS 合成、对话历史访问、用户身份/征信查询。
**修复:** 添加 JWT/API Key 认证中间件，WebSocket 握手时验证 token。

### C-02. MinIO 凭据硬编码在源码中
**组件:** agent-flow
**位置:** `src/config.py:19-20`
**问题:** `minio_access_key: str = "admin"`, `minio_secret_key: str = "changeme123"` 硬编码为默认值。
**修复:** 改为从环境变量必填，无默认值，启动时校验非空。

### C-03. 路径遍历 — TTS 音频路径构造
**组件:** agent-flow
**位置:** `main.py:137`
**问题:** `os.path.join(temp_dir, f"{call_id}_response.wav")` — `call_id` 来自用户输入，可注入 `../../` 写入任意路径。
**修复:** 校验 `call_id` 格式 (`^[a-zA-Z0-9_-]+$`) 或使用 `uuid` 替代。

### C-04. 路径遍历 — WebSocket 读取文件
**组件:** agent-flow
**位置:** `src/ws/handler.py:128-133`
**问题:** `_read_file(path)` 直接打开用户可控路径，无目录限制检查。
**修复:** 解析绝对路径后验证在允许的目录范围内。

### C-05. 路径遍历 — TTS biz_type 缓存路径
**组件:** agent-tts
**位置:** `ttsadapter/engines/cosyvoice/engine.py:48-49`, `ttsadapter/engines/vibevoice/engine.py:40-41`
**问题:** `biz_type` 来自用户输入，直接拼入 `os.path.join(self._cache_dir, biz_type, ...)`，可注入 `../`。
**修复:** 校验 `biz_type` 在白名单 `{"customer_service", "collection", "marketing"}` 内。

### C-06. 无界后台任务 — 内存泄漏
**组件:** agent-asr, agent-tts
**位置:** `agent-asr/asradapter/main.py:61`, `agent-tts/ttsadapter/main.py:51,65`
**问题:** `asyncio.create_task()` 无引用保存、无取消机制。高并发下任务堆积导致内存泄漏，上传失败静默丢弃。
**修复:** 维护任务集合 `set[asyncio.Task]`，添加 `done_callback` 自动清理，shutdown 时取消并等待。

### C-07. 音频文件无大小限制 — DoS
**组件:** agent-asr
**位置:** `asradapter/main.py:54-55`
**问题:** `audio.read()` 无大小校验，攻击者可发送超大文件耗尽内存。
**修复:** 读取前检查 `Content-Length`，读取后检查字节长度，超限返回 413。

### C-08. JSON 输入无解析校验
**组件:** agent-asr, agent-tts, agent-flow
**位置:** `agent-asr/asradapter/main.py:56`, `agent-tts/ttsadapter/main.py:46,60`, `agent-flow/src/clients/mcp.py:48`
**问题:** `json.loads(params)` 直接解析用户输入，无 try-except、无 schema 校验。恶意 JSON 导致 500 错误。
**修复:** 添加 `try-except json.JSONDecodeError`，用 Pydantic 模型校验字段。

### C-09. WebSocket 音频缓冲区无上限
**组件:** agent-flow
**位置:** `src/ws/handler.py:56`
**问题:** `audio_buffer.extend(frame)` 无大小限制，若 VAD 未检测到静音，缓冲区无限增长导致 OOM。
**修复:** 添加 `MAX_AUDIO_BUFFER = 10 * 1024 * 1024`，超限时清空并重置 VAD。

### C-10. 同步阻塞 I/O 在异步函数中
**组件:** agent-tts
**位置:** `ttsadapter/engines/cosyvoice/engine.py:61-62,75-76`, `ttsadapter/engines/vibevoice/engine.py:50-51,69-70`
**问题:** `with open()` 同步文件读写在 `async` 函数中，阻塞事件循环，高并发时吞吐量严重下降。
**修复:** 使用 `aiofiles` 或 `asyncio.to_thread()`。

### C-11. Java 版本不匹配
**组件:** mcp-server
**位置:** `pom.xml:17-20`
**问题:** 声明 `java.version=25` 但系统仅 Java 17，无法编译。
**修复:** 安装 JDK 25 或降级至 `java.version=17`（Spring Boot 3.5 支持）。

---

## HIGH 问题（17 项）

### H-01. Redis 连接池未关闭
**组件:** agent-flow
**位置:** `src/memory/redis_memory.py:8`
**问题:** `aioredis.from_url()` 创建连接池但无 `close()` 方法，shutdown 时连接泄漏。
**修复:** 添加 `async def close()` 方法并在 lifespan shutdown 中调用。

### H-02. HTTP 客户端每次请求新建
**组件:** agent-flow
**位置:** `src/clients/tts.py:17`, `src/clients/asr.py:17`
**问题:** 每次请求创建 `httpx.AsyncClient`，未复用连接池，浪费 TCP 连接。
**修复:** 在 `__init__` 中创建 client 并复用，添加 `async def close()`。

### H-03. 全局可变状态
**组件:** agent-flow, agent-tts, agent-asr
**位置:** `agent-flow/src/graph/flow.py:23-26`, `agent-flow/src/llm/service.py:125`, `agent-tts/ttsadapter/main.py:15`
**问题:** 模块级全局变量（`_assembler`, `_mcp_client`, `engine = None`），难以测试，多实例冲突。
**修复:** 使用 FastAPI `app.state` 或依赖注入。

### H-04. 竞态条件 — 全局缓存无锁
**组件:** agent-asr
**位置:** `asradapter/main.py:15-24`
**问题:** `_audio_cache` (OrderedDict) 在并发请求下无锁保护，数据可能损坏。
**修复:** 使用 `asyncio.Lock` 保护读写。

### H-05. LLM 结构化输出解析脆弱
**组件:** agent-flow
**位置:** `src/llm/service.py:101-122`
**问题:** `_parse_fallback` 用正则从 LLM 响应提取 JSON，脆弱且可能提取错误值。
**修复:** 使用更健壮的 JSON 提取器，或限制 LLM 仅返回合法 JSON。

### H-06. LLM Service 单例非线程安全
**组件:** agent-flow
**位置:** `src/llm/service.py:128-132`
**问题:** `get_llm_service()` 无锁保护，并发调用可能创建多个实例。
**修复:** 使用 `asyncio.Lock` 或模块级初始化。

### H-07. 用户输入直接拼入 LLM 提示 — 注入风险
**组件:** agent-flow
**位置:** `src/graph/flow.py:206`
**问题:** 用户输入直接加入 LLM 消息，未做清洗。恶意用户可注入 prompt。
**修复:** 剥离控制字符，添加系统提示约束。

### H-08. 缺少输入校验 — user_key（手机号）
**组件:** agent-flow
**位置:** `main.py:62`
**问题:** `user_key` 传入 MCP 和记忆模块前无格式校验。
**修复:** 在 `SpeechRequest` 模型中添加手机号正则校验。

### H-09. 模型推理无超时
**组件:** agent-asr, agent-tts
**位置:** `agent-asr/asradapter/engines/sensevoice/engine.py:38-42`, `agent-tts/ttsadapter/engines/cosyvoice/engine.py:67`
**问题:** `model.generate()` / `model.inference_sft()` 同步阻塞调用无超时，GPU 挂死则请求永远挂起。
**修复:** 用 `asyncio.wait_for(asyncio.to_thread(...), timeout=30)` 包裹。

### H-10. TTS 文本无长度限制
**组件:** agent-tts
**位置:** `ttsadapter/main.py:45,59`
**问题:** 无 `text` 长度校验，超长文本导致 OOM 或 GPU 长时间占用。
**修复:** 添加 `max_length=10000` 校验。

### H-11. TTS 缓存逻辑跨引擎重复
**组件:** agent-tts
**位置:** `ttsadapter/engines/cosyvoice/engine.py:44-49`, `ttsadapter/engines/vibevoice/engine.py:36-41`
**问题:** 缓存键生成、路径构造、读写逻辑在两个引擎中重复，违反 DRY。
**修复:** 抽取到基类或工具模块。

### H-12. MCP 端点无认证
**组件:** mcp-server
**位置:** `src/main/resources/application.yaml:22`
**问题:** `/mcp` 端点公开可访问，任何人可查询用户身份和征信信息。
**修复:** 添加 API Key 或 JWT 认证过滤器。

### H-13. 用户 ID 哈希碰撞风险
**组件:** mcp-server
**位置:** `src/main/java/com/trans/mcp/service/UserService.java:20`
**问题:** `Math.abs(phone.hashCode() % 100000)` 存在碰撞且 `Integer.MIN_VALUE` 取绝对值溢出。
**修复:** 使用 `UUID.nameUUIDFromBytes()` 或更安全的哈希。

### H-14. biz_type 无校验
**组件:** mcp-server
**位置:** `src/main/java/com/trans/mcp/service/UserService.java:14`
**问题:** `biz_type` 文档限定 3 个值但代码未校验。
**修复:** `Set.of("customer_service", "collection", "marketing").contains(biz_type)` 校验。

### H-15. 征信结果硬编码
**组件:** mcp-server
**位置:** `src/main/java/com/trans/mcp/service/CreditService.java:15-19`
**问题:** 永远返回 `credit_qualified=true`，绕过真实征信查询。
**修复:** 对接真实数据源或标记为测试模式并添加 feature flag。

### H-16. MinIO 上传失败静默丢弃
**组件:** agent-asr
**位置:** `asradapter/main.py:60-61`
**问题:** 后台上传无错误处理，失败后音频数据永久丢失，调用方无感知。
**修复:** 添加错误回调或改为同步等待上传完成。

### H-17. 敏感信息明文写入日志
**组件:** agent-flow
**位置:** `src/ws/handler.py:39`
**问题:** `user_key`（手机号）明文打印到日志。
**修复:** 脱敏处理：`user_key[:3] + "****" + user_key[-3:]`。

---

## MEDIUM 问题（16 项）

| # | 组件 | 位置 | 问题 | 修复建议 |
|---|------|------|------|----------|
| M-01 | agent-flow | `src/config.py` | `minio_secret_key` 默认值 `"changeme123"` 可能误用于生产 | 移除默认值，启动时校验 |
| M-02 | agent-flow | `src/rag/retriever.py:44-53` | 原生 SQL 用 `text()` 缺乏类型安全 | 使用 pgvector SQLAlchemy 扩展 |
| M-03 | agent-flow | `src/graph/flow.py:189-194` | 每次调用读取 YAML 提示文件，不必要的 I/O | 启动时缓存或惰性加载 |
| M-04 | agent-flow | `src/ws/vad.py:31` | VAD 静音阈值 500.0 RMS 硬编码 | 改为配置项 |
| M-05 | agent-flow | `main.py:137-139` | TTS 临时文件未删除，磁盘空间泄漏 | 用 `NamedTemporaryFile(delete=True)` |
| M-06 | agent-flow | `src/ws/handler.py:52` | WebSocket receive 无超时，僵尸连接堆积 | 添加 receive 超时 |
| M-07 | agent-flow | 全部端点 | 无请求速率限制 | 添加 `slowapi` 限流 |
| M-08 | agent-flow | `main.py` | 无 CORS 配置 | 按需添加 `CORSMiddleware` |
| M-09 | agent-flow | `src/llm/service.py:121` | LLM 响应内容写入日志可能泄露用户信息 | 仅记录解析失败，不记录原文 |
| M-10 | agent-tts | 引擎代码 | 缓存目录 `/data/tts_cache` 无限增长 | 添加 TTL/LRU 淘汰策略 |
| M-11 | agent-tts | `ttsadapter/engines/cosyvoice/engine.py:67-69` | `for chunk in ...: break` 逻辑不清晰 | 使用 `next(iter(...))` |
| M-12 | agent-tts | 测试文件 | CosyVoice 测试 mock 了不存在的 httpx | 调整测试为 mock 模型推理 |
| M-13 | agent-asr | `asradapter/engines/sensevoice/engine.py:44` | confidence 硬编码 0.95 | 从模型输出提取真实置信度 |
| M-14 | agent-asr | `asradapter/main.py:69-74` | 元数据查询无速率限制，可枚举 call_id | 添加认证或速率限制 |
| M-15 | mcp-server | `UserService.java`, `CreditService.java` | 无审计日志记录 PII 访问 | 添加 SLF4J 日志 |
| M-16 | 全部 | MinIO 连接 | 默认 `secure=false`，HTTP 明文传输凭据 | 生产环境默认 `secure=true` |

---

## LOW 问题（10 项）

| # | 组件 | 位置 | 问题 |
|---|------|------|------|
| L-01 | agent-asr | `asradapter/main.py:16` | `_CACHE_MAX = 10000` 魔数，无文档说明 |
| L-02 | agent-asr | 引擎代码 | 信号量模式跨引擎重复，应抽取到基类 |
| L-03 | agent-tts | `engines/cosyvoice/engine.py:68` | 采样率 22050 硬编码 |
| L-04 | agent-tts | `store/storage.py:29` | `build_object_key` 返回 `str | None` 不一致 |
| L-05 | agent-flow | 多文件 | 日志级别使用不一致 |
| L-06 | agent-flow | `src/graph/flow.py:165` | `settings.rag_max_retries + 1` 魔数 |
| L-07 | agent-flow | `src/memory/store.py:11` | `datetime.now()` 非UTC |
| L-08 | agent-flow | `main.py:152-154` | 健康检查仅验证标志，不检查 DB/Redis 连通性 |
| L-09 | mcp-server | `application.yaml:26` | 生产环境使用 DEBUG 日志级别 |
| L-10 | mcp-server | model/*.java | Record 缺少 compact constructor 空值校验 |

---

## 架构评估

### 优势
1. **清晰的插件架构** — ASR/TTS 引擎通过 ABC + importlib 实现可插拔
2. **LangGraph 7 节点流水线** — 每个节点职责单一
3. **业务类型隔离** — 在 Redis/DB/Prompt 层面隔离三种业务
4. **Agentic RAG** — 自适应检索 + 文档评分 + 查询改写设计合理
5. **SQLAlchemy 2.0 异步** — 现代 ORM 用法

### 主要架构问题
1. **全局服务耦合** — `flow.py` 和 `service.py` 中的模块级全局变量导致紧耦合
2. **无依赖注入** — 服务通过 `set_services()` 手动绑定
3. **单例模式滥用** — `LLMService`, `RedisHotMemory` 等单例使测试困难
4. **跨组件重复** — ASR/TTS 有大量结构相似代码（引擎加载、缓存、存储）

---

## 修复优先级建议

**第一阶段（立即）:** C-01 认证, C-02 凭据, C-03~C-05 路径遍历, C-07 文件大小限制, C-09 缓冲区限制
**第二阶段（本周）:** C-06 后台任务, C-08 JSON 校验, C-10 异步 I/O, H-01~H-09
**第三阶段（两周内）:** 其余 HIGH 和 MEDIUM 问题
**第四阶段（按需）:** LOW 问题
