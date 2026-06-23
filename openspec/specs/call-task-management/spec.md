# call-task-management Specification

## Purpose
TBD - created by archiving change align-prompt-config-pipeline. Update Purpose after archive.
## Requirements
### Requirement: 外呼任务定义模型

系统 SHALL 提供 `callbot.call_task` 表存储外呼任务定义,字段含 `tenant_id`、`name`、`prompt_id`(引用 `prompt_config.id`)、`kb_ids`(JSONB)、`status`、`concurrent_limit`、`allowed_hours`、`redial_strategy`(JSONB)、`dept_id`(映射 biz_type)及审计列。`prompt_id` MUST 外键引用 `prompt_config.id`,作为外呼场景三维度 `(tenant_id, biz_type, scenario)` 的来源。

#### Scenario: 创建外呼任务绑定提示词
- **WHEN** 管理员创建外呼任务并选择提示词 X
- **THEN** 系统 SHALL 写入 `call_task`,`prompt_id=X`,记录任务名/策略参数

#### Scenario: 任务定义不含执行态
- **WHEN** 创建/编辑外呼任务
- **THEN** 系统 SHALL 仅持久化定义字段,**不得**触发 originate、调度或重拨执行

### Requirement: 外呼任务 CRUD(定义层)

系统 SHALL 在 Console 提供外呼任务的创建/查询/编辑/删除 API,受 Better Auth 认证与 `calltask:*` RBAC 守护,按 `tenant_id` 隔离。`status` 字段在本期仅作定义存储(idle/running/paused/completed),无执行引擎驱动状态流转。

#### Scenario: 任务编辑
- **WHEN** 管理员编辑任务的并发上限或时段策略
- **THEN** 系统 SHALL 更新 `call_task` 对应字段,本期不产生任何外呼动作

### Requirement: 多租户隔离(外呼任务)

系统 SHALL 按 `tenant_id` 隔离外呼任务。管理员仅可见/操作本租户任务;外呼任务绑定的 `prompt_id` MUST 属于同一 `tenant_id`。

#### Scenario: 跨租户提示词绑定拒绝
- **WHEN** A 租户管理员创建任务时绑定 B 租户的 promptId
- **THEN** 系统 SHALL 拒绝并返回错误

### Requirement: 执行边界声明

系统 SHALL 在本能力范围内**明确排除**外呼执行能力:不实现 originate 发起、不实现调度器、不实现重拨/并发/时段执行逻辑。这些属独立后续变更。本期 `call_task` 的策略字段(`concurrent_limit`/`allowed_hours`/`redial_strategy`)仅为声明性存储,不被任何执行器消费。

#### Scenario: 无执行副作用
- **WHEN** 任务 status 被设为 running
- **THEN** 系统 SHALL 仅更新数据库字段,不发起任何外呼

