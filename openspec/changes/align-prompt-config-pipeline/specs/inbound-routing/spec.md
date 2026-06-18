# Spec: 呼入 DID 路由

> 能力:把呼入被叫号(DID)解析为 `(tenant_id, biz_type, scenario)`,由 `callbot.inbound_route` 路由表驱动,Console 运营;agent-flow 在 CHANNEL_ANSWER 时查表解析并透传。

## ADDED Requirements

### Requirement: DID 路由表

系统 SHALL 维护 `callbot.inbound_route` 路由表,将 DID(精确号)或号段(`did_pattern`)映射到 `(tenant_id, biz_type, scenario)`。每条记录 MUST 含 `is_active` 状态与 `description`。精确号(`did`)MUST 优先于号段(`did_pattern`)匹配;精确号 `did` MUST 唯一。

#### Scenario: 精确号匹配
- **WHEN** 呼入被叫号为 `8001`,路由表存在 `did=8001` 记录
- **THEN** 系统 SHALL 返回该记录的 `(tenant_id, biz_type, scenario)`

#### Scenario: 号段兜底匹配
- **WHEN** 呼入被叫号无精确 `did` 匹配,但有 `did_pattern` 正则命中
- **THEN** 系统 SHALL 返回号段映射的三元组

#### Scenario: 无匹配
- **WHEN** 呼入被叫号在路由表中无任何匹配
- **THEN** 系统 SHALL 拒绝该通话或回落到默认场景,并记录 WARNING

### Requirement: 路由解析时机

系统 SHALL 在 agent-flow 端解析路由:dialplan 仅取 `destination_number`(DID)设为 channel 变量(与 `user_key`),agent-flow 在 ESL `CHANNEL_ANSWER` 处理时查询 `inbound_route` 解析出三元组。dialplan MUST 保持哑,不内置路由逻辑。系统 MAY 预留 dialplan 静态回退(直接设三元组 channel var)用于过渡。

#### Scenario: agent-flow 查表解析
- **WHEN** ESL CHANNEL_ANSWER 触发
- **THEN** agent-flow SHALL 读 `variable_did`,查 `inbound_route` 得 `(tenant_id, biz_type, scenario)`,注册进 `ActiveCallRegistry`

### Requirement: 路由表运营(Console)

系统 SHALL 在 Console 提供 `inbound_route` 的 CRUD 运营能力,受 Better Auth 认证与 `route:*` RBAC 守护,按 `tenant_id` 隔离(管理员仅运营本租户路由)。

#### Scenario: 运营新增路由
- **WHEN** 管理员新增 `did=8004 → (tenant=A, marketing, activation)`
- **THEN** 系统 SHALL 写入 `inbound_route`,下次该 DID 呼入即按新路由解析

#### Scenario: 路由变更即时生效
- **WHEN** 管理员修改某 DID 的 scenario 映射
- **THEN** 系统 SHALL 写库即生效(路由查询直查 DB,无缓存层;或变更即清路由缓存)
