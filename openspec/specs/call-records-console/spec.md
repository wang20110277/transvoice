# call-records-console Specification

## Purpose
TBD - created by archiving change add-call-records-and-recording. Update Purpose after archive.
## Requirements
### Requirement: call 四表 Drizzle 只读映射

系统 SHALL 在 `console/server/src/db/schema.ts` 新增 `callSession`/`callTurn`/`callEvent`/`callArtifact` 四个 Drizzle 表映射，放 `callbot` schema，列名（snake_case DB 列）与 agent-flow `src/db/models.py` 的 SQLAlchemy 模型严格一致。映射 SHALL 为只读（Console 不写这四表），不修改 DDL（表由 agent-flow alembic 维护）。

#### Scenario: 列名与 SQLAlchemy 对齐
- **WHEN** Drizzle 映射定义完成
- **THEN** callSession SHALL 含 `call_id`/`fs_uuid`/`biz_type`/`tenant_id`/`user_id`/`user_key`/`phone_hash`/`phone_masked`/`start_ts`/`end_ts`/`hangup_cause`/`result_code`/`recording_notice_played` 等列
- **AND** 每列的 DB 列名 SHALL 与 models.py 的 mapped_column 字符串完全一致
- **AND** TS 属性名 SHALL 为 camelCase（callId/fsUuid/bizType），与现有 promptConfig/inboundRoute 约定一致

#### Scenario: 不复制 DDL 约束
- **WHEN** 定义 Drizzle 映射
- **THEN** SHALL NOT 重复声明 PrimaryKeyConstraint / Index（索引由 alembic DDL 维护）
- **AND** SHALL 仅声明列与表名，供 select 查询使用

### Requirement: 通话列表 API 按租户隔离

系统 SHALL 提供 `GET /api/calls`，受 `call:view` 权限守卫，返回当前活跃租户（`activeTenantId`）下的通话列表。SHALL 支持查询参数筛选：`biz_type`（精确）、`phone_masked`（模糊）、`start_from`/`start_to`（时间范围）、`page`/`page_size`（分页），默认按 `start_ts DESC` 排序。

#### Scenario: 列表按活跃租户隔离
- **WHEN** 已登录用户（任意角色）请求 GET /api/calls
- **THEN** 系统 SHALL 仅返回 `tenant_id = activeTenantId` 的 call_session 行
- **AND** SHALL NOT 跨租户泄漏其他租户的通话

#### Scenario: 多条件筛选
- **WHEN** 请求带 biz_type=marketing&phone_masked=138&page=2&page_size=20
- **THEN** 系统 SHALL 返回该租户下 marketing 业务、手机号含 "138"、第 2 页（每页 20 条）的通话
- **AND** SHALL 返回 total 计数供前端分页

#### Scenario: 未登录或无权限拒绝
- **WHEN** 未登录请求 GET /api/calls
- **THEN** 系统 SHALL 返回 401
- **WHEN** 登录但角色无 call:view 权限
- **THEN** 系统 SHALL 返回 403

### Requirement: 通话详情聚合 API

系统 SHALL 提供 `GET /api/calls/:id`（id = call_session.id bigserial），返回该通话的聚合视图：session 基本信息 + call_turn 列表（按 ts ASC）+ call_event 列表（按 ts ASC）+ call_artifact 列表（含 kind='recording'）。SHALL 校验该通话 tenant_id 与 activeTenantId 一致，不一致返回 404（不泄漏存在性）。

#### Scenario: 返回完整通话详情
- **WHEN** 已登录用户请求 GET /api/calls/:id，且该通话属于当前活跃租户
- **THEN** 系统 SHALL 返回 { session, turns[], events[], artifacts[] }
- **AND** turns SHALL 按 ts 升序（对话时序）
- **AND** events SHALL 按 ts 升序（事件时序）

#### Scenario: 跨租户访问返回 404
- **WHEN** 用户请求属于其他租户的 call_session.id
- **THEN** 系统 SHALL 返回 404（非 403，避免泄漏该通话存在）
- **AND** SHALL NOT 返回任何通话内容

### Requirement: 录音播放 presigned URL API

系统 SHALL 提供 `GET /api/calls/:id/recording-url`，返回该通话录音的 MinIO presigned URL（1h 有效），供前端 `<audio>` 播放。无 kind='recording' artifact 或 MinIO 未配置时 SHALL 返回 404。

#### Scenario: 返回录音播放地址
- **WHEN** 通话有 kind='recording' artifact 且 MinIO 已配置
- **THEN** 系统 SHALL 返回 { url: <presigned_url>, expiresIn: 3600 }
- **AND** URL 有效期 SHALL 为 1 小时

#### Scenario: 无录音返回 404
- **WHEN** 通话无 recording artifact（未录音或 MinIO 未配置）
- **THEN** 系统 SHALL 返回 404
- **AND** console 详情页 SHALL 显示"录音未归档"

#### Scenario: 跨租户请求录音 URL
- **WHEN** 用户请求其他租户通话的 recording-url
- **THEN** 系统 SHALL 返回 404（与详情 API 隔离一致）

### Requirement: 权限码与菜单项

系统 SHALL 在 `permissions.ts` 新增 `call:view` 权限码，授予 admin / editor / viewer / platform_admin（platform_admin 为超集自动通过）。`ConsoleShell.MENUS` 的 `records`（通话记录）项 SHALL 从 `enabled: false`（下期）改为 `enabled: true`，`href: '/calls'`。

#### Scenario: 通话记录菜单可见
- **WHEN** 任意已登录用户（admin/editor/viewer/platform_admin）进入 console
- **THEN** 侧栏 SHALL 显示可点击的「通话记录」菜单项（enabled=true，无"下期"标记）
- **AND** 点击 SHALL 导航至 /calls

#### Scenario: 通话记录权限分配
- **WHEN** 检查角色权限
- **THEN** admin/editor/viewer/platform_admin SHALL 均具备 call:view 权限
- **AND** 该权限 SHALL 控制所有 /api/calls/* 端点访问

### Requirement: 通话列表页 UI

系统 SHALL 提供 `/calls` 列表页，展示通话表格（开始时间 / biz_type / 手机号 phone_masked / 时长 / 挂断原因 / **操作**）+ 筛选区（biz_type 下拉、手机号搜索、时间范围）+ 分页控件。

表格 SHALL 含「操作」列，每行一个「查看详情」按钮（导航至 `/calls/[id]`）；SHALL NOT 整行点击跳转（避免误触，移除 `<tr onClick>`）。

分页 SHALL 始终显示（即使 `total ≤ pageSize`），并提供页码按钮（当前页高亮，支持跳转，首尾省略号如 `1 2 3 … N`），不仅限于上一页/下一页。

#### Scenario: 操作列查看详情
- **WHEN** 用户点击某行「操作」列的「查看详情」按钮
- **THEN** SHALL 导航至 `/calls/[id]`
- **AND** SHALL NOT 因点击行的其他区域而跳转（整行无 onClick）

#### Scenario: 页码分页始终显示
- **WHEN** 用户访问 /calls（无论 total 多少）
- **THEN** 分页栏 SHALL 始终显示
- **AND** SHALL 提供页码按钮（当前页高亮），点击跳转至该页
- **AND** SHALL 显示总条数与总页数

#### Scenario: 列表展示与筛选
- **WHEN** 用户访问 /calls
- **THEN** 页面 SHALL 调 `GET /api/calls` 加载当前租户通话
- **AND** SHALL 提供筛选（biz_type / 手机号 / 时间范围），提交后刷新列表并回到第 1 页

#### Scenario: 空态
- **WHEN** 当前租户无通话记录
- **THEN** 列表页 SHALL 显示空态提示（"暂无通话记录"）

### Requirement: 通话详情页 UI

系统 SHALL 提供 `/calls/[id]` 详情页，包含：(1) 顶部录音播放器（`<audio controls>`，源为 `/api/calls/:id/recording-url` 返回的 presigned URL，无录音显示"录音未归档"）；(2) 逐轮对话回放区（call_turn 按 ts 升序，user/assistant 交替气泡展示）；(3) 事件时间线区（call_event 按 ts 升序，展示 barge-in/handoff/hangup_by_bot 等）。

#### Scenario: 逐轮对话回放
- **WHEN** 用户访问 /calls/[id]
- **THEN** 页面 SHALL 调用 GET /api/calls/:id 加载聚合数据
- **AND** SHALL 将 turns 按 ts 升序渲染为 user（右）/ assistant（左）交替气泡
- **AND** SHALL 在事件时间线区标注 barge-in/handoff 等关键事件

#### Scenario: 录音播放
- **WHEN** 通话有录音 artifact
- **THEN** 详情页顶部 SHALL 渲染 `<audio>` 播放器
- **AND** 播放器 src SHALL 为 presigned URL（加载时实时获取）
- **WHEN** 通话无录音
- **THEN** SHALL 显示"录音未归档"占位（不渲染播放器）

### Requirement: 通话详情页手动归档按钮

console 通话详情页（`CallDetail.tsx`）SHALL 在检测到该通话无 `kind='recording'` 的 artifact 时，于录音区展示「手动归档」按钮；已归档通话 SHALL NOT 显示该按钮（正常显示播放器）。

点击按钮 SHALL 调用 console 后端 `POST /api/calls/{id}/archive-recording`。成功（200）后 SHALL 自动重载详情，录音播放器出现；失败 SHALL 按状态码给出对应中文提示（404 / 409 / 410 / 502），按钮 SHALL 保留以支持重试。

#### Scenario: 未归档通话显示按钮
- **WHEN** 详情页加载完成，该通话 artifacts 中无 `kind='recording'`
- **THEN** 录音区 SHALL 显示「手动归档」按钮（毗邻或替代「录音未归档」文字）

#### Scenario: 已归档不显示按钮
- **WHEN** 该通话已有 `kind='recording'` artifact
- **THEN** SHALL 显示播放器
- **AND** SHALL NOT 显示「手动归档」按钮

#### Scenario: 归档成功自动刷新
- **WHEN** 用户点击按钮，接口返回 200
- **THEN** 前端 SHALL 重新拉取 detail（`callsApi.detail`）
- **AND** SHALL 自动取得 recordingUrl 并显示播放器
- **AND** SHALL 显示「归档成功」提示

#### Scenario: 失败按状态码提示并可重试
- **WHEN** 接口返回 410（文件已清理）或 502（MinIO 不可用）
- **THEN** 前端 SHALL 显示对应中文提示（「录音文件已被清理，无法补归档」/「归档服务暂不可用，请稍后重试」）
- **AND** 「手动归档」按钮 SHALL 保留可再次点击

#### Scenario: 归档进行中禁用按钮
- **WHEN** 用户点击按钮后请求未返回
- **THEN** 按钮 SHALL 进入 loading 态并禁用
- **AND** SHALL NOT 允许重复点击发起并发请求

### Requirement: console 后端归档转发路由

console 后端 SHALL 提供 `POST /api/calls/{id}/archive-recording` 路由：先按 `activeTenantId` 校验 session 归属（跨租户 → 404，不泄漏存在性），再转发 `POST {CALLBOT_FLOW_URL}/calls/{fsUuid}/archive-recording` 到 agent-flow，透传状态码与 error body。`CALLBOT_FLOW_URL` SHALL 可配置（默认 `http://127.0.0.1:8000`）。

#### Scenario: 租户隔离转发
- **WHEN** 用户请求归档非本租户的通话
- **THEN** 路由 SHALL 返回 404（不泄漏存在性）
- **AND** SHALL NOT 转发至 agent-flow

#### Scenario: 透传 agent-flow 状态
- **WHEN** agent-flow 返回 410 / 502 / 409 / 200
- **THEN** console 路由 SHALL 透传相同状态码与 error body 给前端

#### Scenario: 未配置 flow 地址
- **WHEN** `CALLBOT_FLOW_URL` 未配置
- **THEN** 系统 SHALL 使用默认 `http://127.0.0.1:8000`
- **AND** SHALL NOT 启动报错

