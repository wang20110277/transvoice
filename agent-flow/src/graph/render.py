"""提示词变量渲染。

vars_context 来源(运行时聚合):
- MCP 身份查询(user_key)
- 记忆系统(Redis 热记忆 + PG 长期记忆)
- 外呼 call_task.vars(仅外呼路径)

渲染策略:
- 替换 {name} 占位符;declared 缺省时从模板自身扫描所有 {name} 标记
- 模板含占位符但运行时缺失该变量 → 保留占位符原样 + WARNING(可观测,不崩)
"""
import logging
import re

logger = logging.getLogger(__name__)

# 匹配 {name} 形式的占位符,name 为字母数字下划线(排除 {{ }} 转义与 JSON 片段)
_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _scan_placeholders(template: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(template)


def render(template: str, vars_context: dict, declared: list[str] | None = None) -> str:
    """渲染模板占位符 {name}。

    Args:
        template: 含 {name} 占位符的提示词原文
        vars_context: 运行时变量值字典
        declared: 占位符变量名列表;缺省时从模板扫描所有 {name} 标记。
                  命中 context 则替换;模板中存在但 context 缺失 → WARNING 并保留占位符。
    """
    if not template:
        return template

    keys = declared if declared is not None else _scan_placeholders(template)
    rendered = template

    for key in keys:
        placeholder = "{" + key + "}"
        if key in vars_context:
            rendered = rendered.replace(placeholder, str(vars_context[key]))
        elif placeholder in rendered:
            logger.warning(
                "Prompt variable missing at runtime: var=%s (kept literal placeholder)", key
            )

    return rendered
