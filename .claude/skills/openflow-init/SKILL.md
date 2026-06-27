---
name: openflow-init
description: "OpenFlow init: Initialize project context and OpenSpec config. Visibility alias for openflow init."
argument-hint: "[optional context]"
---

# openflow-init

这是 `openflow init` 的补全可见别名。

执行时必须按以下方式处理：

1. 将本次调用视为用户调用了 `/openflow init $ARGUMENTS`
2. 读取同级 skills 目录中的 `openflow/SKILL.md`
3. 读取 `openflow/init.md`
4. 严格遵守主 openflow 工作流、阶段写入边界和当前阶段文件

5. 先执行主工作流中的项目初始化守卫：在任何项目扫描、需求分析、创建 change 之前检查 `openspec/config.yaml`
6. 如果 `openspec/config.yaml` 已存在，不要提示 init，直接继续 init 阶段
7. 如果缺失，先询问用户是否执行 `/openflow init`；用户跳过时继续 init 阶段并说明没有项目级 config 约束
8. 如果 `$ARGUMENTS` 中有额外需求或上下文，将它作为 init 阶段输入
