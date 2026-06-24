# Tasks: console 首页与通话记录优化

> 按执行依赖排序。console 栈：Next.js 15 App Router + Drizzle + tailwind，无图表库。
> 验证：每个 task 后 `cd console/server && npm run lint`（tsc --noEmit）；UI 改动 `pm2 restart console`。

## 1. 统计 service + API
> 数据层先于 UI。

- [ ] 1.1 `console/server/src/lib/stats-service.ts`（新建）：`getStats(tenantId)` → `{ overview:{ today, total, avgDurationMs, byBizType }, trend:[{date,count}×7] }`。Drizzle 聚合（仿 `calls-service.ts` 的 `listCalls`）：count(*) / avg(EXTRACT EPOCH FROM (end_ts-start_ts)*1000) / group by biz_type / group by date_trunc('day',start_ts) 近7天；5 查询 `Promise.all`
- [ ] 1.2 `console/server/src/app/api/stats/route.ts`（新建）：`GET` → `requirePermission('call:view')` → `getStats(auth.tenantId)` → 返回 JSON（仿 `app/api/calls/route.ts`）
- [ ] 1.3 验证：`npm run lint`；登录后 `curl localhost:3001/api/stats` 返回 overview + trend 结构正确

## 2. 首页组件 + 菜单 + 入口
> 依赖 Task 1 API。

- [ ] 2.1 `console/server/src/components/HomeTrendChart.tsx`（新建）：纯 CSS 柱状图，props `{ data: {date,count}[] }`，柱高 `count/max*100%`，hover 显示日期+数量（title 或 tooltip）
- [ ] 2.2 `console/server/src/components/HomePage.tsx`（新建，client）：`fetch('/api/stats')` → 概览卡片（今日/累计/平均时长/biz_type 分布）+ `<HomeTrendChart>`；空态处理
- [ ] 2.3 `console/server/src/app/page.tsx`（改）：`redirect('/prompts')` → 渲染 `<ConsoleShell ...><HomePage/></ConsoleShell>`（保留未登录 `redirect('/login')`）；ConsoleShell props（tenantId/userEmail/userName/role）参考 `/calls/page.tsx`
- [ ] 2.4 `console/server/src/components/ConsoleShell.tsx`（改）：MENUS dashboard 项 → `{ key:'home', label:'首页', icon: LayoutDashboard, href:'/', enabled:true }`，移到数组第一位
- [ ] 2.5 验证：`npm run lint`；`pm2 restart console`；登录后默认进首页，菜单第一项「首页」，概览卡片 + 趋势图展示

## 3. 通话记录列表：页码分页 + 操作列
> 独立于 Task 1/2。

- [ ] 3.1 `console/server/src/components/CallRecordsList.tsx`（改）：去掉 `<tr onClick>` 整行跳转；表头加「操作」列，每行「查看详情」按钮（`router.push('/calls/[id]')`）
- [ ] 3.2 `console/server/src/components/CallRecordsList.tsx`（改）：分页完善为页码分页（始终显示 + 页码按钮，当前页高亮，首尾省略号），移除 `total > pageSize` 显示条件
- [ ] 3.3 验证：`npm run lint`；`pm2 restart console`；/calls 操作列按钮跳详情、整行不跳、页码可跳转

## 4. 端到端验证

- [ ] 4.1 首页：登录默认进入，概览数字与 call_session 实际一致，趋势图近7天柱正确
- [ ] 4.2 多租户：切租户，首页统计与通话记录仅显示该租户数据
- [ ] 4.3 通话记录：操作列详情、页码分页、筛选 + 录音回放回归正常
- [ ] 4.4 `openspec validate enhance-console-home-and-records --strict` 通过
