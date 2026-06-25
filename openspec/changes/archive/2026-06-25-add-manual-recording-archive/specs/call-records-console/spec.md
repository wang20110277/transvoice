# call-records-console Specification

## ADDED Requirements

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
