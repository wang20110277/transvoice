"""TTSOutputBuffer.recent_reverse — 镜像最近发往 FreeSWITCH 的帧，供 AEC 做远端参考。"""
import asyncio
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
sys.path.insert(0, str(_SRC))

from ws.jitter_buffer import TTSOutputBuffer, SILENCE_FRAME  # noqa: E402


def _make_buf(sent):
    async def send(b):
        sent.append(b)
    return TTSOutputBuffer(send_fn=send)


def test_recent_reverse_defaults_to_silence():
    sent = []
    buf = _make_buf(sent)
    assert buf.recent_reverse == SILENCE_FRAME


def test_recent_reverse_updates_to_sent_frame():
    async def main():
        sent = []
        buf = _make_buf(sent)
        await buf.start()
        payload = b"\x01" * 960  # 一个 30ms 帧
        buf.write(payload)
        # 让 _send_loop 把这一帧发出去（> 一个 frame_interval 即可），
        # 但必须 < frame_interval 的 2 倍，否则静音填充会覆盖镜像。
        await asyncio.sleep(0.05)
        await buf.stop()
        assert sent, "no frame sent"
        assert sent[0] == payload  # 确认发出去的就是 TTS 帧
        assert buf.recent_reverse == payload

    asyncio.run(main())


def test_recent_reverse_becomes_silence_during_gap():
    async def main():
        sent = []
        buf = _make_buf(sent)
        await buf.start()
        buf.write(b"\x01" * 960)
        await asyncio.sleep(0.12)
        # 不再 write，进入静音填充窗口 → 发静音帧 → recent_reverse 归零
        await asyncio.sleep(0.12)
        await buf.stop()
        assert buf.recent_reverse == SILENCE_FRAME

    asyncio.run(main())
