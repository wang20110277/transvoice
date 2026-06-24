# call-records-console Specification

## MODIFIED Requirements

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
