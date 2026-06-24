# console-home-dashboard Specification

## ADDED Requirements

### Requirement: 首页菜单与默认入口

系统 SHALL 在 `ConsoleShell.MENUS` 将原「数据看板」项改为「首页」：`label:'首页'`、`enabled:true`、`href:'/'`，并置于菜单第一位。`app/page.tsx` SHALL 在登录态渲染首页统计组件（未登录重定向 `/login`），不再重定向到 `/prompts`。

#### Scenario: 首页菜单排首位
- **WHEN** 任意已登录用户进入 console
- **THEN** 侧栏第一项 SHALL 为「首页」（enabled=true，无"下期"标记）
- **AND** 点击 SHALL 导航至 `/`

#### Scenario: 登录默认进入首页
- **WHEN** 已登录用户访问 `/`（根路径）
- **THEN** SHALL 渲染首页统计组件（不再重定向到 /prompts）
- **WHEN** 未登录访问 `/`
- **THEN** SHALL 重定向至 `/login`

### Requirement: 首页统计 API

系统 SHALL 提供 `GET /api/stats`，受 `call:view` 权限守卫，按 activeTenantId 隔离，返回当前租户的通话统计：
- `overview`：今日通话数（`start_ts ≥ 当日0点`）、累计通话数、平均通话时长（`end_ts - start_ts` 均值，单位 ms）、按 biz_type 分组计数
- `trend`：近 7 天每日通话量（按 `start_ts` 的 date 分组，日期升序）

#### Scenario: 返回概览与趋势
- **WHEN** 已登录用户（具备 call:view）请求 `GET /api/stats`
- **THEN** SHALL 返回 `{ overview: { today, total, avgDurationMs, byBizType }, trend: [{ date, count } × 7] }`
- **AND** SHALL 仅统计 `tenant_id = activeTenantId` 的 call_session

#### Scenario: 租户隔离
- **WHEN** 请求 `GET /api/stats`
- **THEN** SHALL 仅含当前活跃租户数据
- **AND** SHALL NOT 跨租户泄漏

#### Scenario: 未登录或无权限拒绝
- **WHEN** 未登录请求 `GET /api/stats`
- **THEN** SHALL 返回 401
- **WHEN** 登录但无 call:view 权限
- **THEN** SHALL 返回 403

### Requirement: 首页统计 UI

系统 SHALL 在首页渲染：
1. 概览卡片：今日通话数、累计通话数、平均通话时长、biz_type 分布
2. 近 7 天每日通话量趋势图（纯 CSS 柱状图，无图表库依赖，柱高按 `count / maxCount` 比例，hover 显示日期 + 数量）

#### Scenario: 概览卡片
- **WHEN** 用户访问首页
- **THEN** SHALL 调 `GET /api/stats` 加载
- **AND** SHALL 展示今日 / 累计通话数、平均时长、biz_type 分布卡片

#### Scenario: 近 7 天趋势
- **WHEN** 首页加载统计
- **THEN** SHALL 渲染 7 个柱（近 7 天每日通话量）
- **AND** 柱高 SHALL 按 `count / 最大值` 比例
- **AND** hover 柱 SHALL 显示日期与数量

#### Scenario: 空态
- **WHEN** 当前租户无通话
- **THEN** 概览 SHALL 显示 0，趋势柱 SHALL 全为 0 高度（不报错）
