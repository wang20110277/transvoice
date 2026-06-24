# Design: console 首页与通话记录优化

## 背景

console 通话记录列表（`CallRecordsList.tsx`）整行 `onClick` 跳转易误触、分页仅 `total>20` 显示且无页码；首页是禁用占位（`ConsoleShell.tsx:33`），`app/page.tsx` 仅重定向 `/prompts`。本变更补齐这两块。

## 关键决策

### D1: 统计 API — GET /api/stats，复用 call:view 权限
新增 `lib/stats-service.ts` + `app/api/stats/route.ts`。受 `call:view` 守卫（与 `/api/calls` 一致，数据源同为 call_session，admin/editor/viewer/platform_admin 均有此权限，无需新权限码）。返回：
```json
{
  "overview": { "today": N, "total": N, "avgDurationMs": N, "byBizType": {"customer_service":N,"collection":N,"marketing":N} },
  "trend": [ {"date":"2026-06-18","count":N}, ...近7天... ]
}
```
tenant 隔离（activeTenantId），仿 `calls-service.ts` 的 `listCalls` 模式。

### D2: 统计查询 — Drizzle 聚合，5 查询 Promise.all 并发
- `overview.total`：`count(*) WHERE tenant_id`
- `overview.today`：`count(*) WHERE tenant_id + start_ts >= 当日0点`
- `overview.avgDurationMs`：`avg(EXTRACT(EPOCH FROM (end_ts - start_ts)) * 1000) WHERE tenant_id + end_ts NOT NULL`（PG interval → 秒 → ms）
- `overview.byBizType`：`group by biz_type, count(*) WHERE tenant_id`
- `trend`：`group by date_trunc('day', start_ts), count(*) WHERE tenant_id + start_ts >= now - 7d`

5 个轻查询 `Promise.all` 并发（总耗时≈最慢一个）。不合并（可读性优先）。avgDuration / date_trunc 用 `sql` 模板（Drizzle 不直接支持 PG interval 运算）。

### D3: 近 7 天趋势 — 纯 CSS 柱状图，零依赖
新建 `components/HomeTrendChart.tsx`。7 个 `div` 柱，高度 `= count/maxCount * 100%`。tailwind 样式。hover 显示日期+数量（title 属性或自定义 tooltip）。不引入 recharts。

### D4: 首页组件与菜单
- `ConsoleShell.MENUS`：dashboard 项改为 `{ key:'home', label:'首页', icon: LayoutDashboard, href:'/', enabled:true }`，移到数组第一位。
- `app/page.tsx`：从 `redirect('/prompts')` 改为渲染 `<ConsoleShell ...><HomePage/></ConsoleShell>`（保留未登录 `redirect('/login')`）。server component，ConsoleShell 的 props（tenantId/userEmail/userName/role）从 session 取，参考 `/calls/page.tsx` 的传参模式。
- `components/HomePage.tsx`（client）：fetch `/api/stats` → 概览卡片 + `<HomeTrendChart>`。

### D5: 通话记录 — 页码分页 + 操作列
- `CallRecordsList.tsx`：
  - 分页：始终显示分页栏 + 页码按钮（当前页高亮，首尾省略号，如 `1 2 3 … N`）；移除 `total > pageSize` 显示条件。
  - 操作列：表头加「操作」，每行「查看详情」按钮（`router.push('/calls/[id]')`）；去掉 `<tr onClick>`。
- 后端 `/api/calls` 不变（已支持 page/pageSize + total）。

## 改动清单

| 文件 | 改动 |
|---|---|
| `console/server/src/lib/stats-service.ts` | 新建：overview + trend 聚合 |
| `console/server/src/app/api/stats/route.ts` | 新建：GET /api/stats，call:view 守卫 |
| `console/server/src/components/HomeTrendChart.tsx` | 新建：纯 CSS 柱状图 |
| `console/server/src/components/HomePage.tsx` | 新建：概览卡片 + 趋势图 |
| `console/server/src/app/page.tsx` | 改：重定向 → 首页组件 |
| `console/server/src/components/ConsoleShell.tsx` | 改：MENUS dashboard→home 首页第一位 |
| `console/server/src/components/CallRecordsList.tsx` | 改：页码分页 + 操作列，去行点击 |

## 风险与缓解

- **统计查询性能**：实时聚合，5 查询并发。单租户通话量级小，无需缓存（边界已声明）。未来量大再加物化视图。
- **app/page.tsx server component 改造**：需正确传 ConsoleShell props，参考 `/calls/page.tsx`（已用同模式）。
- **avgDuration / date_trunc SQL**：用 Drizzle `sql` 模板写 PG 原生表达式，跨 PG 版本兼容（EXTRACT EPOCH / date_trunc 均标准）。
- **空租户**：overview 全 0、trend 7 个 0 柱，UI 显示空态而非报错。
