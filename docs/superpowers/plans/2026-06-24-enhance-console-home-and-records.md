# 实现计划：console 首页与通话记录优化

> 来源：openspec/changes/enhance-console-home-and-records/plan-ready.md
> 类型：全新实现（前端 + 后端 API）。验证：`cd console/server && npm run lint`；UI 改动 `pm2 restart console`。
> 模式参考：`calls-service.ts`（Drizzle 聚合）、`app/api/calls/route.ts`（requirePermission）、`guards.ts`（AuthCtx.tenantId）、`sql\`...\`` 模板（schema.ts 已用）。
> 提交策略：不自动 commit，实现完由用户决定。

## Task 1: 统计 service + API
- [x] 1.1 `console/server/src/lib/stats-service.ts`（新建）：getStats(tenantId) → {overview, trend}。5 查询 Promise.all（count / avg EXTRACT EPOCH / group by biz_type / date_trunc 近7天）
- [x] 1.2 `console/server/src/app/api/stats/route.ts`（新建）：GET → requirePermission('call:view') → getStats(auth.tenantId) → JSON
- [x] 1.3 验证：npm run lint；curl /api/stats 返回 overview+trend

## Task 2: 首页组件 + 菜单 + 入口（依赖 Task 1）
- [x] 2.1 `components/HomeTrendChart.tsx`（新建）：纯 CSS 柱状图，柱高 count/max*100%，hover 日期+数量
- [x] 2.2 `components/HomePage.tsx`（新建，client）：fetch /api/stats → 概览卡片 + HomeTrendChart
- [x] 2.3 `app/page.tsx`（改）：redirect('/prompts') → ConsoleShell+HomePage（未登录 redirect /login），props 参考 /calls/page.tsx
- [x] 2.4 `components/ConsoleShell.tsx`（改）：MENUS dashboard→home 首页第一位
- [x] 2.5 验证：lint；pm2 restart console；登录默认进首页

## Task 3: 通话记录页码分页 + 操作列（独立）
- [x] 3.1 `components/CallRecordsList.tsx`：去 tr onClick，加操作列（查看详情按钮）
- [x] 3.2 `components/CallRecordsList.tsx`：分页改页码分页（始终显示+页码+省略号），移除 total>pageSize
- [x] 3.3 验证：lint；pm2 restart console；操作列跳详情、整行不跳、页码可跳

## Task 4: 端到端验证（依赖 Task 1-3）
- [x] 4.1 首页概览数字 + 趋势图正确
- [x] 4.2 多租户隔离
- [x] 4.3 通话记录回归
- [x] 4.4 openspec validate --strict 通过
