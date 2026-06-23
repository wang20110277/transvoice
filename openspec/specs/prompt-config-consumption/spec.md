# prompt-config-consumption Specification

## Purpose
TBD - created by archiving change align-prompt-config-pipeline. Update Purpose after archive.
## Requirements
### Requirement: 三维度提示词加载

系统 SHALL 提供 `get_system_prompt(tenant_id, biz_type, scenario)` 加载接口,以 `(tenant_id, biz_type, scenario)` 为键。Redis 缓存 key MUST 统一为 `cb:prompt:{tenant_id}:{biz_type}:{scenario}`,TTL 5 分钟。缓存未命中时 SHALL 回源 DB(`is_active=true` 记录)并回填 Redis。任一级失败 MUST 降级而非抛出(最终返回空串 + 告警)。

#### Scenario: 缓存命中
- **WHEN** 呼入命中 Redis 缓存
- **THEN** 系统 SHALL 直接返回缓存内容,不查 DB

#### Scenario: 缓存未命中回源
- **WHEN** Redis 无该键
- **THEN** 系统 SHALL 查 DB 取 active 记录,返回内容并回填 Redis(TTL 5min)

#### Scenario: 缓存与 DB 均无
- **WHEN** Redis 与 DB 均无该 `(tenant_id, biz_type, scenario)` 记录
- **THEN** 系统 SHALL 返回空串并记录 WARNING,不中断通话

### Requirement: 缓存失效接口

系统 SHALL 提供 `invalidate_prompt_cache(tenant_id, biz_type, scenario)`,删除对应 Redis key。Console 发布动作 MUST 触发该失效(共享同一 Redis 实例直删),使配置变更零延迟生效。

#### Scenario: 发布触发失效
- **WHEN** Console publish 写库完成
- **THEN** 系统 SHALL `DEL cb:prompt:{tenant_id}:{biz_type}:{scenario}`
- **AND** 下次呼入 MUST 取到新版本

### Requirement: 变量渲染

系统 SHALL 在加载提示词后、组装 LLM 消息前,执行变量渲染 `render(template, vars_context)`。`vars_context` MUST 聚合:① MCP 身份查询结果(基于 `user_key`)② 记忆系统(Redis 热记忆 + PG 长期记忆)③ 外呼 `call_task.vars`(仅外呼路径有)。渲染 SHALL 替换 `extra.variables` 声明的占位符。

#### Scenario: 变量正常渲染
- **WHEN** 模板含 `{customer_name}`,且 `vars_context` 含该键
- **THEN** 系统 SHALL 将占位符替换为实际值后送 LLM

#### Scenario: 变量缺失可观测
- **WHEN** 模板含 `{arrears_days}`,但 `vars_context` 无该键
- **THEN** 系统 SHALL 保留占位符原样,记录 WARNING 日志(含 tenant/scenario/缺失变量名),不中断通话

### Requirement: 呼入维度全链路透传

系统 SHALL 在呼入路径全程透传 `(tenant_id, biz_type, scenario, user_key)`:dialplan 取 DID → ESL CHANNEL_ANSWER 解析 → `ActiveCallRegistry` 注册 → `CallGraphState` 携带 → `run_streaming_pipeline` 加载提示词。`flow.py` 调用加载接口时 MUST 传入完整三元组,不得使用 `default` 兜底值替代 `tenant_id`/`scenario`。

#### Scenario: 呼入携带完整维度
- **WHEN** 一通呼入进入 `run_streaming_pipeline`
- **THEN** `get_system_prompt` SHALL 收到从 `state` 透传来的 `tenant_id`/`biz_type`/`scenario`,而非硬编码默认值

### Requirement: 多租户隔离(消费端)

系统 SHALL 按 `tenant_id` 隔离提示词加载。A 租户呼入 MUST 只能命中 A 租户的提示词;Redis key 与 DB 查询 MUST 以 `tenant_id` 为过滤条件。

#### Scenario: 租户隔离加载
- **WHEN** A 租户呼入请求提示词
- **THEN** 查询条件 MUST 含 `tenant_id=A`,绝不返回 B 租户记录

