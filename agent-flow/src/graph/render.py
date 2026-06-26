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


def parse_call_target_vars(raw: str | None) -> dict[str, str]:
    """解析 call_target.vars（key:value|key:value 字符串）为 render 用的 dict。

    DB 列为 TEXT 纯字符串（不使用 JSON），由 console serializeVars 写入、此处解析。
    规则：
    - 按 '|' 拆对，每对按【首个】':' 拆 key/value（允许值含 ':'，如时间 09:30）
    - trim key/value；跳过空对、无 ':' 的对、空 key
    - 空串/None → {}；全程不抛异常（运行时容错）
    """
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split("|"):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        key, _, value = pair.partition(":")
        key = key.strip()
        if not key:
            continue
        out[key] = value.strip()
    return out


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
