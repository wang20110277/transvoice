# Proposal: console 首页与通话记录优化

## Why

console 当前有两个体验缺口：

1. **通话记录列表**（`CallRecordsList.tsx`）整行 `onClick` 跳转详情易误触，缺明确操作入口；分页仅 `total>20` 时出现（`CallRecordsList.tsx:172`），数据少时不可见，且只有上/下页、无页码跳转。
2. **首页/数据看板**是禁用占位（`ConsoleShell.tsx:33` `enabled:false` 无 href），`app/page.tsx` 仅重定向到 `/prompts`，无任何运营概览 —— 运营人员登录后看不到通话量、趋势等关键指标。

## What Changes

### 需求 1：通话记录列表优化
- 加「操作」列（查看详情按钮），去掉整行 `onClick` 跳转（`CallRecordsList.tsx:154`）
- 分页完善为页码分页：始终显示分页栏 + 页码按钮可跳转（如 `1 2 3 … N`）

### 需求 2：首页统计（数据看板 → 首页）
- `ConsoleShell` MENUS：「数据看板」改名「首页」，`enabled:true`，`href:'/'`，移到菜单第一位
- `app/page.tsx`：从重定向（`/prompts`）改为首页统计组件（登录态校验保留）
- 新建首页统计 UI：基础概览（今日通话数、累计通话数、平均通话时长、biz_type 分布）+ 近 7 天每日通话量趋势图（纯 CSS 柱状图，零依赖）
- 后端新增统计 API（`call_session` 聚合），tenant_id 隔离

## 成功标准

- [ ] 通话记录：操作列「查看详情」按钮跳详情；整行不再跳转；分页始终显示且支持页码跳转
- [ ] 首页：菜单第一项「首页」，登录后默认进入；展示今日/累计通话数、平均时长、biz_type 分布、近 7 天趋势
- [ ] 统计 API：tenant_id 隔离，跨租户不泄露；数据来源 call_session（只读聚合）
- [ ] 趋势图：近 7 天每日通话量，纯 CSS 柱状图（无新依赖）
- [ ] 现有通话记录筛选 / 详情 / 录音回放功能不受影响

## 边界

- 仅 inbound call_session 数据（call_task 无执行引擎，外呼不产生 session）
- 统计为实时聚合查询，不做预计算 / 缓存（数据量级未到需缓存）
- 不引入图表库（tailwind 纯 CSS 柱状图）；趋势只做近 7 天通话量
- 权限沿用现状（spec 确认首页是否需独立权限码，还是所有登录用户可见）

## 约束

- Next.js 15 App Router + Drizzle ORM（console 现有栈）
- 统计查询走 console pg 池（与 agent-flow 同 callbot schema，只读）
- 聚合仿 `calls-service.ts` 的 `listCalls` 模式（count / eq / gte / lte）
- 近 7 天趋势：按 `start_ts` 的 date 分组 `COUNT(*)`，`WHERE tenant_id + start_ts >= now-7d`
