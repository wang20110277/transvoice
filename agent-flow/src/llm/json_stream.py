"""增量 JSON 解析器 — 从 LLM token 流中逐步提取结构化字段"""
import json
import re
from dataclasses import dataclass, field


@dataclass
class StreamEvent:
    """LLM 流式输出的单个事件"""
    partial_text: str = ""
    text_delta: str | None = None
    action: str | None = None
    is_complete: bool = False
    parsed: dict | None = None


class IncrementalJSONParser:
    """从 LLM token 流中增量提取 JSON 字段。

    目标 schema: {"action": "say|ask|handoff|end", "text": "...", "intent": "...", "labels": {...}}
    主要关注:
    - action 字段: 枚举值，通常最先输出
    - text 字段: 增量提取文本内容（送入句子拆分器）
    """

    _ACTION_RE = re.compile(r'"action"\s*:\s*"(\w+)"')
    _TEXT_OPEN_RE = re.compile(r'"text"\s*:\s*"')
    _ESCAPE_RE = re.compile(r'\\(["\\/bfnrt])')

    def __init__(self) -> None:
        self._buffer = ""
        self._action: str | None = None
        self._full_text: str = ""
        self._text_started = False
        self._text_ended = False
        self._complete = False

    def feed(self, token: str) -> list[StreamEvent]:
        """喂入一个 token，返回产生的事件列表。"""
        self._buffer += token
        events: list[StreamEvent] = []

        # 尝试提取 action（只需提取一次）
        if self._action is None:
            m = self._ACTION_RE.search(self._buffer)
            if m:
                self._action = m.group(1)
                events.append(StreamEvent(action=self._action))

        # 尝试增量提取 text
        if not self._text_ended:
            text_delta = self._extract_text_delta()
            if text_delta:
                self._full_text += text_delta
                events.append(StreamEvent(
                    partial_text=self._full_text,
                    text_delta=text_delta,
                    action=self._action,
                ))

        return events

    def finalize(self) -> StreamEvent:
        """流结束，尝试完整 JSON 解析。"""
        self._complete = True
        parsed = None
        try:
            # LLM 可能在 JSON 前后附加额外文本
            json_match = re.search(r'\{[^{}]*\}', self._buffer, re.DOTALL)
            if json_match:
                raw = json_match.group(0)
                # 处理 JSON 中可能的转义问题
                raw = self._ESCAPE_RE.sub(r'\1', raw)
                parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            parsed = {
                "action": self._action or "say",
                "text": self._full_text or "",
            }

        # 确保完整 text 被捕获
        if parsed and not self._full_text:
            self._full_text = parsed.get("text", "")

        return StreamEvent(
            partial_text=self._full_text,
            action=self._action or (parsed.get("action") if parsed else "say"),
            is_complete=True,
            parsed=parsed,
        )

    def _extract_text_delta(self) -> str:
        """从 buffer 中增量提取 text 字段的新内容。"""
        # 找到 "text": " 的起始位置
        if not self._text_started:
            m = self._TEXT_OPEN_RE.search(self._buffer)
            if not m:
                return ""
            self._text_started = True
            self._text_start = m.end()
            self._last_text_end = m.end()
            # 检查 buffer 中是否已有内容
            return self._scan_text_content()

        # 已经在 text 中，从上次位置继续扫描
        return self._scan_text_content()

    def _scan_text_content(self) -> str:
        """从 _last_text_end 扫描到新的文本内容。"""
        if not self._text_started:
            return ""

        start = getattr(self, '_last_text_end', self._text_start)
        buf = self._buffer
        delta_chars: list[str] = []
        i = start

        while i < len(buf):
            ch = buf[i]

            # 遇到未转义的引号 → text 字段结束
            if ch == '"' and (i == 0 or buf[i - 1] != '\\'):
                self._text_ended = True
                self._last_text_end = i + 1
                return "".join(delta_chars)

            # 遇到反斜杠转义
            if ch == '\\' and i + 1 < len(buf):
                next_ch = buf[i + 1]
                escape_map = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\'}
                delta_chars.append(escape_map.get(next_ch, next_ch))
                i += 2
                continue

            delta_chars.append(ch)
            i += 1

        self._last_text_end = i
        return "".join(delta_chars)
