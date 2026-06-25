"""外呼 originate 命令构造 — 把三元组 + 任务/号码 ID 注入 channel vars。

设计为纯函数（无副作用），便于单测：接收轻量 dataclass 描述的被叫与任务，
返回 FreeSWITCH `originate` 命令串。三元组从 call_task.prompt_config 反查后传入。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OutboundTarget:
    """被叫号码上下文（来自 call_target 行）。"""
    target_id: int
    user_key: str            # 明文号码/key，渲染与 user_key 透传
    phone: str               # 拨号用号码（端点模板 {phone} 占位）


@dataclass
class OutboundContext:
    """外呼三维度 + 任务/号码关联（call_task + prompt_config 解析后传入）。"""
    tenant_id: str
    biz_type: str
    scenario: str
    task_id: int


def build_originate_command(
    target: OutboundTarget,
    ctx: OutboundContext,
    endpoint_template: str = "user/{phone}@{domain}",
    domain: str = "",
    codec_string: str = "PCMA",
    caller_id: str = "",
) -> str:
    """构造 originate 命令（纯函数，端点模板/编解码/主叫号均可注入，便于单测）。

    channel vars 注入 ai_outbound 标记 + 三元组 + call_task_id/call_target_id/user_key；
    answer 处理器据此走 outbound 分支（跳过 DID 解析），复用 inbound 对话管线。

    endpoint_template 默认 user/{phone}@{domain}：本地注册分机必须直连（sofia/internal/{phone}
    会重新进 dialplan 触发循环/deflect）。codec_string 默认 PCMA：实测 G.722 会让
    mod_audio_fork 抓到的帧格式不对、ASR 收不到有效音频，必须强制线性编解码。
    本函数不感知被叫是分机还是真实号码 —— 测试(内部分机)→生产(真实号码,走
    sofia/gateway/<gw>/{phone})的完整改造清单见 src/config.py「外呼执行引擎」注释。
    """
    vars_ = [
        "ai_outbound=true",
        f"call_task_id={ctx.task_id}",
        f"call_target_id={target.target_id}",
        f"tenant_id={ctx.tenant_id}",
        f"biz_type={ctx.biz_type}",
        f"scenario={ctx.scenario}",
        f"user_key={target.user_key}",
        "ignore_early_media=true",
    ]
    if codec_string:
        vars_.append(f"absolute_codec_string={codec_string}")
    if caller_id:
        vars_.append(f"origination_caller_id_number={caller_id}")

    endpoint = endpoint_template.format(phone=target.phone, domain=domain)
    # B-leg 用 playback silence_stream 维持持续 write media path——实测 &park() 的 channel
    # 不产生持续 write 帧，mod_audio_fork 下行 PCM 无载体播不到 RTP（你听不到 AI）。
    # 必须像呼入一样用 silence_stream 保活，AI 下行音频才能搭便车播到软电话。
    return f"originate {{{','.join(vars_)}}}{endpoint} &playback(silence_stream://-1)"
