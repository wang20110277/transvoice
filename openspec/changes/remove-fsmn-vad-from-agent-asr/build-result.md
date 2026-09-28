# Build Result: remove-fsmn-vad-from-agent-asr

执行方式：superpowers:subagent-driven-development（每 task 独立实现者 + 双裁决审查 + 终审全分支审查）
分支：`remove-fsmn-vad-from-agent-asr`（d1ae8b3..00a0946，7 commits）

## 验证证据（终审独立复跑核实）

- agent-asr：3 passed（原 15——12 个 VAD 用例随 `vad_segmenter.py` 删除）
- agent-flow：105 passed（= 基线 102 − 3 旧 stt + 5 新 stt + 1 新 config），首跑全绿无抖动
- **R1 竞态未命中**：`turn_detection="vad"` 首跑即过且稳定复跑，EOU 等 final 机制如设计生效，回退预案未启用
- 终审（全分支）：Ready to merge — 零 Critical/Important；协议对称性、spec 10 场景、D1-D5 决策、范围隔离全部核实

## 过程裁决（Rulings）

1. **分支隔离**：SDD 禁止未经同意在 main 实现 → 建 local 分支，收尾呈现合并选项。
2. **`_SINGLE_ATTEMPT_CONN_OPTIONS` 删除**（design 曾写保留）：随 stream() 路径删除后为死代码，按项目规范删除。
3. **Task 3 grep 模式过宽**：`_SINGLE_ATTEMPT` 误中 tts_plugin 自有常量（范围外合法）——以「STT 侧零残留」为标准。
4. **Task 5 brief #8 自相矛盾**：替换文本字面含 "FSMN-VAD" 违反其自身 grep 门——批准等义改写。
5. **Task 5 grep 门语义**：config.py:86 / session.py:89 的 FSMN 字面为有意 WHY 历史注记——门意图是「无过期架构描述」，排除刻意注记后零命中。
6. **Task 5 config.py:105 范围扩展**：asr_ws_url 注释过期属真实缺口，Step-4 门（绑定要求）优先于 3 文件清单。
7. **Task 5「逐 call 自建连接」同类残留**（Important）：与裁决 6 同类过期表述，修复而非延后（CLAUDE.md 两处 + README 一处）。

## 延后项（终审 triage 全部 DEFER，供 close / 后续变更消化）

**建议带入 close 或小后续变更：**
- 服务端音频累积无上限（`ws_server.py` `audio.extend`）——设计 R3 有意信任客户端（VAD 60s 天然封顶），但混版本部署（旧 flow 不发 end）会积累整场音频（~57MB/30min）；~5 行 cap 即可加固。
- `_recognize_impl` 的 `ws.recv()` 无超时——基类 `STT.recognize` 不强制 `conn_options.timeout`，活进程 wedged 会使该轮次永挂；需按音频时长缩放的 ceiling，属刻意后续项。
- 锁步部署注记：协议两侧无兼容层，CLAUDE.md 部署节补一行「agent-asr 与 agent-flow 需同版本部署/回滚」（两方向失配均降级不崩溃，已由终审推演）。

**测试/外观类（DEFER）：**
- `ConnectionClosedOK`-before-result 分支无测试（与已测两分支同类错误分类）
- `CALLBOT_VAD_MIN_SILENCE_DURATION` 无 env 覆盖测试（默认值已断言，pydantic 框架保证）
- `SpeechEvent` 无 request_id（与上游 StreamAdapter 行为一致，仅 metrics 标签）
- FakeBatchSTT 未用 `min_final_len` 参数、`**_ignored` 未用、`recv_bytes` 无精确计数、fake VAD INFERENCE_DONE 无 frames（均测试卫生，零行为影响）
- `ws_server.py` `WebSocketDisconnect` 不可达（starlette 下 receive 返回 dict）——日志级别噪音，预先存在
- 逐帧重采样非整数倍率帧界不连续——预先存在，电话路径 8k/16k 精确 2 倍不受影响

**运维提醒：** 生产冒烟（Task 6 Step 3）因无 GPU/FS 运行时跳过——上线前后建议跑一通真实 SIP 呼叫验证端点体感（R2，无测试可覆盖感知延迟）。

## Task 脉络

| Task | Commit | 审查 |
|---|---|---|
| 1 agent-asr 无状态 | f684263 | clean（2 minor 延后） |
| 2 VAD 配置 | bf50dba | clean（1 minor 延后） |
| 3 STT 非流式 | 911ce89 | clean（3 minor 延后） |
| 4 session 切 vad | 30665a7 | clean（4 minor 延后；R1 未命中） |
| 5 文档同步 | 06bd997/738c4be/00a0946 | 2 轮修复后 clean |
| 6 全量回归 | 无 commit | 3+105 首跑全绿 |
