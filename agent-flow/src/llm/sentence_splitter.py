"""句子拆分器 — 积累 LLM token，按标点拆分为完整句子供 TTS"""
import time
from dataclasses import dataclass


@dataclass
class Sentence:
    text: str
    index: int


class SentenceSplitter:
    """将 LLM token 流拆分为适合 TTS 的句子。

    支持中英文标点，强制拆分过长句子，超时自动刷新。
    """

    SENTENCE_ENDINGS = frozenset('。！？.!?\\n；;')
    MIN_LENGTH = 4
    MAX_LENGTH = 60
    FLUSH_TIMEOUT = 0.3  # 300ms 无新 token 自动刷新

    def __init__(self) -> None:
        self._buffer = ""
        self._index = 0
        self._last_feed_time: float = 0.0

    def feed(self, token: str) -> list[Sentence]:
        """喂入 token，返回拆分出的完整句子列表。"""
        self._buffer += token
        self._last_feed_time = time.monotonic()
        return self._try_split()

    def check_timeout(self) -> list[Sentence]:
        """检查是否超时需要刷新。在有新 token 到来之间周期性调用。"""
        if (self._buffer
                and time.monotonic() - self._last_feed_time > self.FLUSH_TIMEOUT):
            return self._flush_buffer()
        return []

    def flush(self) -> Sentence | None:
        """流结束，刷新残余缓冲区。"""
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return None
        sent = Sentence(text=text, index=self._index)
        self._index += 1
        return sent

    def _try_split(self) -> list[Sentence]:
        """尝试按标点拆分缓冲区。"""
        results: list[Sentence] = []

        while len(self._buffer) >= self.MIN_LENGTH:
            split_pos = self._find_split_pos()
            if split_pos < 0:
                # 无标点但超长 → 强制拆分
                if len(self._buffer) > self.MAX_LENGTH:
                    text = self._buffer[:self.MAX_LENGTH]
                    self._buffer = self._buffer[self.MAX_LENGTH:]
                    results.append(Sentence(text=text, index=self._index))
                    self._index += 1
                break

            text = self._buffer[:split_pos + 1].strip()
            self._buffer = self._buffer[split_pos + 1:]
            if text:
                results.append(Sentence(text=text, index=self._index))
                self._index += 1

        return results

    def _find_split_pos(self) -> int:
        """在缓冲区中查找第一个句末标点的位置。"""
        for i, ch in enumerate(self._buffer):
            if ch in self.SENTENCE_ENDINGS and i >= self.MIN_LENGTH - 1:
                return i
        return -1

    def _flush_buffer(self) -> list[Sentence]:
        """超时刷新：将当前缓冲区作为一句话输出。"""
        text = self._buffer.strip()
        self._buffer = ""
        if not text:
            return []
        sent = Sentence(text=text, index=self._index)
        self._index += 1
        return [sent]
