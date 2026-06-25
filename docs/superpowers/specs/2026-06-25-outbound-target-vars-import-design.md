# 外呼任务结构化导入 + 每号码变量渲染

- **日期**: 2026-06-25
- **状态**: 设计已确认，待实现计划
- **范围**: `console/`（导入 UI + 解析 + 服务层）+ `agent-flow/`（数据模型 + 运行时渲染接线）

## 1. 背景与问题

外呼任务（`call_task`）的号码清单（`call_target`）导入目前是一个 `<textarea>`，每行粘贴一个号码；后端 `bulkCreateFromCsv` 按换行切分、`phone_hash` 去重入库。**每行只存一个号码，别无他物**。

提示词渲染能力（`agent-flow/src/graph/render.py`）已存在：模板含 `{name}` 占位符，`flow.py:388-395` 从 `identity`（MCP）+ `call_task_vars` 聚合 `vars_context` 后渲染。但：

- `call_target` 表**无变量列** —— 号码带的业务信息（姓名/欠款/到期日/产品…）无处可存。
- `call_task_vars` 在 graph state 里**只被读取，全程无人写入**（grep 全仓仅 `flow.py:392` 一处读站点）→ 外呼话术实际拿不到任何每号码变量。

**结果**：导入"太 low"——只能灌号码，每号码业务变量既存不下也渲染不进话术，无法千人千面。

## 2. 目标与非目标

**目标**

1. 导入采用**固定 5 列模板**：`序号 | 业务类型 | 手机号 | 客户id | json`，其中 `json` 列定义 render 渲染字段 key:value。
2. `json` 列 → `call_target.vars`（JSONB）；`客户id` → `call_target.customer_id`（展示/审计）。
3. 外呼摘机后，每号码 `vars` 经 registry 透传进入 graph state 的 `call_task_vars`，由现有 `render()` 渲染进各自话术。
4. 导入前**客户端即时预览**：前 N 行 + 占位符命中/缺失比对（prompt 占位符 ↔ json keys）+ 错误行标注。

**非目标（YAGNI）**

- 不做 biz_type 级规范变量 profile —— render 字段由 `json` 列内容决定，prompt 占位符 ↔ json keys 比对（单一真相源仍是任务绑定 prompt 的占位符）。
- `业务类型`/`客户id`/`序号` **不进渲染**（render 字段只在 `json` 里）。
- `业务类型` 每行可填但仅校验/展示，**不改任务话术**（渲染统一用任务绑定 prompt）。
- 不做单条号码录入的变量编辑（保留单条录入纯号码，向后兼容）。
- 不做已导入 target 的逐条变量编辑（导入即定型）。
- 不改 `render.py`（渲染链路已就绪）。

## 3. 固定模板列语义（关键决策）

导入名单固定 5 列，各列语义：

| 列 | 处理 | 渲染 | 持久化 |
|---|---|---|---|
| 序号 | 导入预览/错误定位行序，列表隐式行号展示 | ❌ | ❌ 不入业务字段 |
| 业务类型 | 导入时校验 = 任务绑定 prompt 的 biz_type（不一致**软警告**，仍可导入）；列表展示任务 biz_type | ❌ | ❌ 不存（= 任务 biz_type） |
| 手机号 | → `user_key` + `phone_hash`（MCP 身份查询基于 phone，**不变**） | ❌ | ✅ 现有列 |
| 客户id | → `call_target.customer_id`，号码清单列表展示/审计 | ❌ | ✅ **新列** |
| json | → `call_target.vars`，render 的 key:value 负载 | ✅ | ✅ **新列** |

**render 字段单一真相源**：任务绑定 prompt 的 `{占位符}` 集合 = 期望被渲染的变量名；`json` 列的 keys 应覆盖这些占位符。预览拿 prompt 占位符与各行 json keys 比对。

**校验严格度**：全程软警告（不阻断）。`json` 与占位符不一致 / `业务类型`不匹配 / json 格式错误 → 警告，允许导入有效行；运行时缺变量保留占位符原样（现有 `render.py` 行为）。

**biz_type 不改话术**：一个 `call_task` 绑定一条 prompt（一个 biz_type）。`业务类型`列只校验一致性 + 展示，渲染统一走任务 prompt。若操作员确实需要混合 biz_type 话术，属另一独立变更（每号码选 prompt），本期不做。

## 4. 设计

### 4.1 数据模型

`call_target` 加两列：

| 位置 | 改动 |
|---|---|
| `agent-flow/src/db/models.py` `CallTarget` | `vars: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)` + `customer_id: Mapped[str \| None] = mapped_column(Text)` |
| `console/server/src/db/schema.ts` `callTarget` | `vars: jsonb('vars').notNull().default(sql\`'{}'::jsonb\`)` + `customerId: text('customer_id')` |
| `agent-flow/alembic/versions/0002_call_target_vars_customer_id.py` | `ALTER TABLE callbot.call_target ADD COLUMN vars JSONB NOT NULL DEFAULT '{}'::jsonb, ADD COLUMN customer_id TEXT`（当前仅 `0001_init_full_schema`，故新迁移编号 0002） |

不加 biz_type / seq 列（前者=任务 biz_type，后者不入库）。`repository.get_call_target`（`repository.py:172`）已 `select(*)` 返回整行，自动带新列，**无需改查询**。vars 不走 channel vars，故 `OutboundTarget`/`originate.py` 不动。

### 4.2 导入与预览（console 侧）

#### 4.2.1 解析（纯函数，新建 `console/server/src/lib/csv-import.ts`）

- 输入：CSV 文本（粘贴或 `FileReader` 读 `.csv` 文件）+ 绑定 prompt 的 `variables: string[]` + 任务 biz_type（用于校验）。
- **固定 5 列表头**（按列名匹配，兼容中英文别名）：`序号/seq`、`业务类型/biz_type`、`手机号/phone`、`客户id/customer_id`、`json/vars`。
- 每行解析：
  - `phone`（手机号）去空白后非空 —— **仅此为硬错误**（不格式强校验，兼容测试分机号 `1000`）。
  - `json` 列：`JSON.parse` 成 `Record<string,string>`；空串/缺省 → `{}`；解析失败 → 行级 error（malformed json）。
  - `biz_type`：与任务 biz_type 比对，不一致 → 行级 warning（不阻断）。
  - `customer_id`：可选，原样留存。
  - `seq`：可选，仅回显。
- 最小 CSV 引号处理：容忍 `"a,b"` 内逗号与两端引号剥离（json 列必然含逗号，必须正确处理）。
- 输出 `ParseResult`：
  ```ts
  {
    rows: { seq?: string; bizType?: string; phone: string; customerId?: string;
            vars: Record<string,string>; error?: string; warning?: string }[],
    totalRows: number, validCount: number, errorCount: number,
    placeholders: { hit: string[]; missing: string[]; extra: string[]; perVarCoverage: Record<string, number> },
  }
  ```
  - `placeholders.hit/missing/extra`：prompt 占位符 ∩ / − / json keys 并集 − 占位符。
  - `perVarCoverage`：每个占位符在多少行 json 中命中（如 `{customer_name: 48}`），辅助判断覆盖度。

#### 4.2.2 预览面板（`CallTasksManager.tsx` 号码清单展开区，替换现有 textarea）

- 两路输入：粘贴文本 / 上传 `.csv`（`<input type="file" accept=".csv">` + `FileReader.readAsText`）。
- **下载模板按钮**：导出固定 5 列空表头 CSV（`序号,业务类型,手机号,客户id,json` + 一行示例），降低操作员格式出错。
- 解析后即时显示：
  - 汇总条：共 N 行 / 有效 M / 错误 K / biz_type 不一致 W。
  - 占位符比对：命中（绿）/ 缺失（黄）/ 多余（灰）+ 覆盖度。
  - 前 5 行表格：序号/业务类型/手机号/客户id/json 摘要；错误行红、warning 行黄。
- 提交按钮在"无表头/无手机号列"时禁用；有错误行但存在有效行时允许提交（仅提交有效行）。

#### 4.2.3 提交（结构化 payload）

- 前端发 `{ targets: { phone: string; customerId?: string; vars: Record<string,string> }[], maxAttempts: number }` → 复用 `POST /api/call-tasks/:id/targets`。
- `callTargetsApi.uploadCsv` 改为 `importStructured(taskId, targets, maxAttempts)`（`call-targets-api.ts`）。`biz_type`/`seq` 不上传（校验已在客户端做，服务端不存）。

#### 4.2.4 后端服务（`call-targets-service.ts`）

- **删除** `bulkCreateFromCsv`（仅 `targets/route.ts` 一个调用方，已核实无其他引用）。
- 新增 `bulkCreateStructured(taskId, tenantId, targets, maxAttempts, email)`：
  - `taskInTenant` 校验（跨租户 → 0 inserted）。
  - 每条：phone 去空白后非空 + `phoneHash`/`phoneMasked` + `customerId`（可选原样）+ `vars` 强制 `Record<string,string>`（API 边界防御：拒对象/数组值，避免复杂类型污染 prompt）。
  - 批量 `insert ... onConflictDoNothing`（任务内 `phone_hash` 去重，与现有一致）→ 返回 `{ inserted, skipped }`。
- `targets/route.ts` POST：body 增加 `targets?: {phone, customerId?, vars}[]` 分支，与 `phone`（单条）互斥。
- `CallTargetDTO` 加 `customerId: string | null`；`toDTO` 透传；号码清单列表加"客户id"列。

### 4.3 运行时渲染接线（agent-flow，方案 A：摘机加载 + registry 透传）

> 仅 `vars` 进渲染；`customer_id`/`biz_type`/`seq` 不进。

1. `ActiveCall`（`src/ws/registry.py`）加 `call_target_vars: dict = field(default_factory=dict)`。
2. `ActiveCallRegistry.register()` 加参数 `call_target_vars: dict | None = None`。
3. `main.py` CHANNEL_ANSWER 外呼分支（~341-348，已读 `call_target_id`）加载 vars：
   ```python
   call_target_vars: dict = {}
   if call_target_id is not None:
       t = await repository.get_call_target(call_target_id)
       if t and isinstance(t.vars, dict):
           call_target_vars = t.vars
   ```
   传入 `_call_registry.register(..., call_target_vars=call_target_vars)`。
4. `run_pre_llm_phase`（`src/graph/flow.py:274`）加参数 `call_task_vars: dict | None = None`，state 写 `"call_task_vars": call_task_vars or {}`（state 组装点 `flow.py:301`）。
5. `StreamingCallHandler._process_streaming_turn` → `_run_pipeline` 调 `self._pre_llm_fn(...)`（`handler.py:550`）时，从 `active_call.call_target_vars` 取值透传 `call_task_vars=`。`active_call` 全程在 `handle()`（`handler.py:124`）作用域内可用。
6. 渲染**已就绪**：`flow.py:392-394` 已读 `state["call_task_vars"]` 并 `vars_context.update(...)` → `render(system_prompt, vars_context)` 替换占位符。**不改 `render.py`**。

呼入路径：无 `call_target_id` → `call_target_vars` 恒 `{}` → 零影响。

### 4.4 错误处理

- **导入**：无表头 / 无手机号列 → 前端禁用提交 + 红字；`json` 列解析失败 → 行级 error（红）；`业务类型`≠任务 biz_type → 行级 warning（黄，不阻断）；部分错误行 → 允许提交有效行。
- **运行时**：`get_call_target` 失败或 `vars` 非 dict → `call_target_vars={}`，降级为"无每号码变量"（=现状，不崩，记 WARNING）；render 缺变量保留占位符 + WARNING（现有行为）。
- **安全**：`vars` 值 console 端强制 string + render 端 `str()`；prompt 是 LLM 文本输入，无 SQL/命令注入面。phone 仍走 `phone_hash` 去重键（明文不进 vars）。

## 5. 测试

- **`csv-import.ts` 纯函数单测**：固定 5 列表头（中/英别名）/ json 列含逗号的引号处理 / json 解析失败行 / 手机号空行错误 / biz_type 不匹配 warning / 占位符 hit/missing/extra + 覆盖度统计 / customer_id 可选透传。
- **`call-targets-service.ts` `bulkCreateStructured`**：vars + customerId 正确落库、`Record<string,string>` 强制（拒对象值）、`phone_hash` 去重、跨租户 0 inserted。
- **agent-flow `registry.py`**：`ActiveCall.call_target_vars` 默认 `{}`、`register` 透传。
- **agent-flow `flow.py`**：`run_pre_llm_phase(call_task_vars={...})` → state 含 `call_task_vars` → `render` 命中 `{customer_name}`（断言渲染后文本无残留占位符）。
- **集成路径**（手动/脚本）：外呼 originate → 摘机 CHANNEL_ANSWER → registry 带 vars → 首轮话术 `{占位符}` 被替换（看 `freeswitch.log` + `system_prompt content` 日志）。

## 6. 改动清单

**console**
- 新建 `src/lib/csv-import.ts`（固定 5 列解析 + json 解析 + 占位符比对纯函数）
- 改 `src/components/CallTasksManager.tsx`（号码清单展开区：文本/文件双输入 + 下载模板 + 预览面板 + 结构化提交 + 列表加"客户id"列）
- 改 `src/lib/call-targets-api.ts`（`importStructured` 替代 `uploadCsv`；DTO 加 `customerId`）
- 改 `src/lib/call-targets-service.ts`（删 `bulkCreateFromCsv`，加 `bulkCreateStructured`；DTO/toDTO 加 `customerId`）
- 改 `src/app/api/call-tasks/[id]/targets/route.ts`（POST 接受 `targets` 分支）
- 改 `src/db/schema.ts`（callTarget 加 `vars` + `customerId`）

**agent-flow**
- 改 `src/db/models.py`（CallTarget 加 `vars` + `customer_id`）
- 新建 `alembic/versions/0002_call_target_vars_customer_id.py`
- 改 `src/ws/registry.py`（`ActiveCall.call_target_vars` + `register` 参数）
- 改 `main.py`（外呼分支加载 vars + 传入 register）
- 改 `src/graph/flow.py`（`run_pre_llm_phase` 加 `call_task_vars` 参数 + state 写入）
- 改 `src/ws/handler.py`（`_run_pipeline` 透传 `call_task_vars`）

**不改**：`render.py`、`originate.py`、`repository.get_call_target`、呼入路径、MCP 身份查询（仍 phone-based）。

## 7. 风险

- **registry 透传链路较长**（answer → register → handle → _process_streaming_turn → _run_pipeline → _pre_llm_fn），任一环节漏传则静默回退为无变量（不崩但话术不个性化）。靠"运行时缺变量保留占位符 + WARNING"可观测，测试覆盖整链。
- **json 列解析**：操作员手写 json 易出错（引号/逗号）。靠预览行级 error + 模板下载降低风险；malformed json 行剔除但保留有效行。
- **JSONB 默认值**：旧迁移已建表，新迁移 `ADD COLUMN vars ... DEFAULT '{}'` 对存量行回填 `{}`，`customer_id` 可空，兼容。
- **vars 体积**：单 target 变量通常 < 1KB，无上限风险；不额外限制（YAGNI）。
