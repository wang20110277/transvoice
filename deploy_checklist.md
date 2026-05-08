# 部署检查清单

## 部署前检查

### 硬件与网络
- [ ] 服务器 CPU ≥ 32 核心
- [ ] 服务器内存 ≥ 64GB
- [ ] 服务器磁盘 ≥ 1TB NVMe
- [ ] 网卡 ≥ 10GbE
- [ ] GPU 已安装（NVIDIA Driver + CUDA）
- [ ] 内网互通（FS ↔ UniMRCP ↔ ASR/TTS/LLM）
- [ ] 端口开放：5060(SIP), 16384-32768(RTP), 8021(ESL), 8060(MRCP)

### 依赖软件
- [ ] FreeSWITCH 已编译安装
- [ ] UniMRCP 已编译安装
- [ ] VibeVoice ASR 服务已部署（GPU0）
- [ ] VibeVoice TTS 服务已部署（GPU1）
- [ ] Qwen3.6 推理服务已部署（GPU2）
- [ ] PostgreSQL 17 已安装并初始化
- [ ] Redis 已安装
- [ ] MinIO 已安装
- [ ] NAS 已挂载到 /nas/rec

### 目录与权限
- [ ] /usr/local/freeswitch/ 目录存在
- [ ] /nas/rec/ 目录存在并可写
- [ ] /var/log/unimrcp/ 目录存在
- [ ] 运行用户有权限读写上述目录
- [ ] 录音告知文件存在：`/usr/local/freeswitch/sounds/legal_notice.wav`

---

## 配置文件部署检查

### 1. FreeSWITCH 配置

#### modules.conf
- [ ] 文件已复制到 /usr/local/freeswitch/conf/
- [ ] 核心模块已加载：`mod_sofia`, `mod_unimrcp`, `mod_event_socket`, `mod_dptools`
- [ ] `fs_cli -x "show modules"` 能看到上述模块

#### vars.xml
- [ ] 文件已复制到 /usr/local/freeswitch/conf/
- [ ] local_ip, external_rtp_ip, external_sip_ip 已配置
- [ ] sip_port=5060, rtp_start=16384, rtp_end=32768
- [ ] handoff_extension=1001 已配置

#### event_socket.conf.xml
- [ ] 文件已复制到 /usr/local/freeswitch/conf/autoload_configs/
- [ ] listen-ip=127.0.0.1, listen-port=8021
- [ ] password 已修改为强密码
- [ ] acl 已配置内网白名单
- [ ] 事件订阅包含：CHANNEL_*, DETECTED_SPEECH, PLAYBACK_*, RECORD_*

#### unimrcp.conf.xml
- [ ] 文件已复制到 /usr/local/freeswitch/conf/autoload_configs/
- [ ] asr_default_v1 profile 指向 UniMRCP
- [ ] tts_*_v1 profiles 已定义
- [ ] recognition-timeout=5000ms

#### dialplan/public.xml
- [ ] 文件已复制到 /usr/local/freeswitch/conf/dialplan/
- [ ] biz_type 变量能正常设置
- [ ] 转接目标 loopback/1001 已配置

---

### 2. UniMRCP 配置

#### unimrcpserver.xml
- [ ] 文件已复制到 /etc/unimrcp/
- [ ] listen-port=8060
- [ ] ASR backend-url 指向 VibeVoice ASR (10.0.0.20:8080)
- [ ] TTS backend-url 指向 VibeVoice TTS (10.0.0.21:8080)
- [ ] 工作线程数 ≥ 8
- [ ] 健康检查端点已配置

#### 服务启动
- [ ] `systemctl start unimrcp` 成功
- [ ] `netstat -tlnp | grep 8060` 显示端口监听
- [ ] `/healthz` 返回 200

---

### 3. VibeVoice 服务

#### ASR (GPU0)
- [ ] 服务运行中（端口 8080）
- [ ] 健康检查 `/healthz` 返回 200
- [ ] 指标端点 `/metrics` 可访问
- [ ] GPU 使用正常（CUDA_VISIBLE_DEVICES=0）

#### TTS (GPU1)
- [ ] 服务运行中（端口 8080）
- [ ] 健康检查 `/healthz` 返回 200
- [ ] 三业务 profile 配置文件独立：
  - /etc/vibevoice-tts/customer_service/profile.yaml
  - /etc/vibevoice-tts/collection/profile.yaml
  - /etc/vibevoice-tts/marketing/profile.yaml
- [ ] GPU 使用正常（CUDA_VISIBLE_DEVICES=1）

---

### 4. Qwen3.6 推理服务 (GPU2)
- [ ] 服务运行中（端口 8080 或自定义）
- [ ] 健康检查正常
- [ ] GPU 使用正常（CUDA_VISIBLE_DEVICES=2）
- [ ] 能正常响应推理请求

---

### 5. 数据层

#### PostgreSQL 17
- [ ] 服务运行中
- [ ] 端口 5432 监听
- [ ] 数据库 callbot 已创建
- [ ] pgvector 扩展已安装
- [ ] DDL 已执行（8 张表）
- [ ] 能正常读写

#### Redis
- [ ] 服务运行中
- [ ] 端口 6379 监听
- [ ] 密码已配置（如有）
- [ ] 能正常读写

#### MinIO
- [ ] 服务运行中
- [ ] 端口 9000 监听
- [ ] bucket 已创建：rec-cs, rec-collection, rec-marketing
- [ ] lifecycle 策略已配置（1-3 年）

---

### 6. Orchestrator

#### 环境准备
- [ ] Python 3.12 环境
- [ ] 依赖包已安装：python-ESL, rocketmq-client-python, langchain, langgraph, redis, psycopg2-binary
- [ ] 配置文件已准备（连接信息）

#### 代码部署
- [ ] 项目代码已部署
- [ ] 模块文件存在：fs_esl.py, fs_actions.py, graph_flow.py, llm_qwen.py 等
- [ ] 能正常导入模块

#### 服务启动
- [ ] systemd 服务文件已配置
- [ ] 服务启动成功
- [ ] ESL 连接成功
- [ ] 能接收 FreeSWITCH 事件

---

## 端到端测试检查

### 1. 通话建立
- [ ] 主叫拨打接入
- [ ] FreeSWITCH 响应 INVITE
- [ ]通话建立（200 OK）

### 2. 录音告知
- [ ] CHANNEL_ANSWER 事件触发
- [ ] 录音告知文件播放
- [ ] 播放成功日志

### 3. 录音启动
- [ ] 录音文件开始生成
- [ ] 双声道文件都存在（caller.wav, bot.wav）

### 4. 语音识别
- [ ] detect_speech 启动成功
- [ ] 用户说话
- [ ] DETECTED_SPEECH 事件回调
- [ ] 识别文本非空

### 5. LLM 决策
- [ ] LLM 服务正常
- [ ] 返回结构化动作
- [ ] 日志记录决策结果

### 6. TTS 播报
- [ ] TTS 服务正常
- [ ] 播报音色符合业务（cs/col/mkt）
- [ ] 语速/音量符合配置

### 7. 记忆写入
- [ ] Redis 状态更新
- [ ] PG turn 表写入
- [ ] 记忆召回正常

### 8. 转接人工
- [ ] 触发转接条件（静默/意图）
- [ ] 执行 transfer 命令
- [ ] 通话成功转接到 1001

### 9. 通话结束
- [ ] CHANNEL_HANGUP 事件触发
- [ ] 录音停止
- [ ] 会话状态更新
- [ ] 记忆 finalize
- [ ] 录音归档到 MinIO

---

## 监控告警检查

### Prometheus 指标
- [ ] FreeSWITCH 指标暴露（端口 9090）
- [ ] UniMRCP 指标暴露
- [ ] VibeVoice 指标暴露
- [ ] Orchestrator 指标暴露

### Grafana 面板
- [ ] 面板已导入
- [ ] 实时数据展示
- [ ] 通话量、成功率、延迟等

### 告警规则
- [ ] 通话失败告警
- [ ] ASR/TTS/LLM 失败告警
- [ ] 录音告知未播放告警
- [ ] 服务宕机告警

---

## 业务隔离检查

### 三业务音色
- [ ] 客服：cs_female_soft_01，语速正常
- [ ] 催收：col_male_serious_01，语速稍慢，音量+1
- [ ] 营销：mkt_female_lively_01，语速稍快

### 数据隔离
- [ ] Redis key 包含 biz_type
- [ ] NAS 目录按 biz_type 分离
- [ ] MinIO bucket 按 biz_type 分离
- [ ] PG 查询带 biz_type 过滤

### 转接隔离
- [ ] 客服转接：loopback/1001（示例）
- [ ] 催收转接：loopback/1001（示例）
- [ ] 营销转接：loopback/1001（示例）

---

## 生产就绪检查

### 安全
- [ ] ESL 密码已修改
- [ ] 服务仅内网暴露
- [ ] 敏感字段不落明文
- [ ] 录音文件权限正确

### 性能
- [ ] 并发测试通过（目标并发数）
- [ ] 延迟满足要求（ASR < 500ms, TTS < 300ms）
- [ ] 无内存泄漏
- [ ] 资源使用稳定

### 可靠性
- [ ] 主备切换测试通过
- [ ] 异常恢复测试通过
- [ ] 数据一致性检查通过

### 运维
- [ ] 监控面板完整
- [ ] 告警规则有效
- [ ] 日志规范统一
- [ ] 备份策略已配置

---

## 部署完成签字

| 检查项 | 检查人 | 日期 | 备注 |
|--------|--------|------|------|
| 硬件与网络 | | | |
| 依赖软件 | | | |
| FreeSWITCH | | | |
| UniMRCP | | | |
| VibeVoice | | | |
| Qwen3.6 | | | |
| 数据层 | | | |
| Orchestrator | | | |
| 端到端测试 | | | |
| 监控告警 | | | |
| 业务隔离 | | | |
| 生产就绪 | | | |

---

*最后更新：2025-05-08*