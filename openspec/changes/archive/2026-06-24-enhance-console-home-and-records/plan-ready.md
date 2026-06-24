# 实现计划：console 首页与通话记录优化

## 来源
- 提案：openspec/changes/enhance-console-home-and-records/proposal.md
- 设计：openspec/changes/enhance-console-home-and-records/design.md
- 规格：openspec/changes/enhance-console-home-and-records/specs/（call-records-console, console-home-dashboard）
- 任务：openspec/changes/enhance-console-home-and-records/tasks.md

## 前置状态
全新实现（无验证补丁）。console 栈 Next.js 15 App Router + Drizzle + tailwind，无图表库。
验证基线：每 task `cd console/server && npm run lint`（tsc --noEmit）；UI 改动 `pm2 restart console`。

---

## 实现步骤

### Task 1: 统计 service + API
- **目标**：首页统计的数据层
- **步骤**：
  1. `console/server/src/lib/stats-service.ts`（新建）：`getStats(tenantId)` → `{ overview:{ today, total, avgDurationMs, byBizType }, trend:[{date,count}×7] }`。Drizzle 聚合仿 `calls-service.ts` 的 `listCalls`：`count(*)` / `avg(sql\`EXTRACT(EPOCH FROM (end_ts-start_ts))*1000\`)` / group by biz_type / group by `sql\`date_trunc('day',start_ts)\`` 近7天；5 查询 `Promise.all`
  2. `console/server/src/app/api/stats/route.ts`（新建）：`GET` → `requirePermission('call:view')` → `getStats(auth.tenantId)` → `NextResponse.json`（仿 `app/api/calls/route.ts`）
- **验证**：`npm run lint`；`curl localhost:3001/api/stats`（登录态）返回 overview + trend 结构正确

### Task 2: 首页组件 + 菜单 + 入口（依赖 Task 1）
- **目标**：首页 UI + 菜单 + 默认入口
- **步骤**：
  1. `console/server/src/components/HomeTrendChart.tsx`（新建）：纯 CSS 柱状图，props `{ data: {date,count}[] }`，柱高 `count/max*100%`，hover 显示日期+数量
  2. `console/server/src/components/HomePage.tsx`（新建，client）：`fetch('/api/stats')` → 概览卡片（今日/累计/平均时长/biz_type 分布）+ `<HomeTrendChart>`；空态（全 0）处理
  3. `console/server/src/app/page.tsx`（改）：`redirect('/prompts')` → `<ConsoleShell ...><HomePage/></ConsoleShell>`（未登录 `redirect('/login')`）；ConsoleShell props 参考 `/calls/page.tsx`
  4. `console/server/src/components/ConsoleShell.tsx`（改）：MENUS dashboard 项 → `{ key:'home', label:'首页', icon: LayoutDashboard, href:'/', enabled:true }`，移到数组第一位
- **验证**：`npm run lint`；`pm2 restart console`；登录默认进首页，菜单首位「首页」，概览 + 趋势图展示

### Task 3: 通话记录页码分页 + 操作列（独立）
- **目标**：通话记录交互优化
- **步骤**：
  1. `console/server/src/components/CallRecordsList.tsx`（改）：去掉 `<tr onClick>` 整行跳转；表头加「操作」列，每行「查看详情」按钮 `router.push('/calls/[id]')`
  2. `console/server/src/components/CallRecordsList.tsx`（改）：分页改页码分页（始终显示 + 页码按钮，当前页高亮，首尾省略号），移除 `total > pageSize` 显示条件
- **验证**：`npm run lint`；`pm2 restart console`；/calls 操作列跳详情、整行不跳、页码可跳转

### Task 4: 端到端验证（依赖 Task 1-3）
- **目标**：功能回归 + 规格校验
- **步骤**：
  1. 首页概览数字与 call_session 实际一致，趋势图近7天柱正确
  2. 多租户：切租户，首页统计 + 通话记录仅显示该租户
  3. 通话记录：操作列详情、页码分页、筛选 + 录音回放回归正常
  4. `openspec validate enhance-console-home-and-records --strict` 通过
