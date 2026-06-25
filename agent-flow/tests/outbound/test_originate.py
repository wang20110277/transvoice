"""build_originate_command 单测 — 验证外呼命令构造契约。"""
import re

from outbound.originate import OutboundContext, OutboundTarget, build_originate_command


def _make_target(phone="1000", target_id=12, user_key="13800138000"):
    return OutboundTarget(target_id=target_id, user_key=user_key, phone=phone)


def _make_ctx(task_id=5, tenant_id="t1", biz_type="marketing", scenario="default"):
    return OutboundContext(tenant_id=tenant_id, biz_type=biz_type, scenario=scenario, task_id=task_id)


def test_command_starts_with_originate_keyword():
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert cmd.startswith("originate ")


def test_command_injects_outbound_flag():
    """ai_outbound 标记驱动 answer 处理器走 outbound 分支。"""
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert "ai_outbound=true" in cmd


def test_command_injects_three_dimensions():
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert "tenant_id=t1" in cmd
    assert "biz_type=marketing" in cmd
    assert "scenario=default" in cmd


def test_command_injects_task_and_target_ids():
    cmd = build_originate_command(_make_target(target_id=12), _make_ctx(task_id=5), domain="192.168.0.192")
    assert "call_task_id=5" in cmd
    assert "call_target_id=12" in cmd


def test_command_injects_user_key():
    cmd = build_originate_command(_make_target(user_key="13800138000"), _make_ctx(), domain="192.168.0.192")
    assert "user_key=13800138000" in cmd


def test_command_uses_endpoint_template_with_phone():
    """默认端点模板渲染 {phone}：user/{phone}@{domain} 直连注册分机（实测结论）。"""
    cmd = build_originate_command(_make_target(phone="1000"), _make_ctx(), domain="192.168.0.192")
    assert "user/1000@192.168.0.192" in cmd


def test_command_uses_custom_gateway_template():
    """后期换 SIP 网关只改模板（不含 domain 占位），命令构造器不感知。"""
    cmd = build_originate_command(
        _make_target(phone="13800138000"), _make_ctx(),
        endpoint_template="sofia/gateway/mygw/{phone}",
    )
    assert "sofia/gateway/mygw/13800138000" in cmd


def test_command_injects_absolute_codec_string_by_default():
    """强制线性编解码 PCMA：实测 G.722 会让 ASR 收不到有效音频。"""
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert "absolute_codec_string=PCMA" in cmd


def test_command_omits_codec_string_when_empty():
    cmd = build_originate_command(_make_target(), _make_ctx(), codec_string="", domain="192.168.0.192")
    assert "absolute_codec_string" not in cmd


def test_command_bleg_app_is_silence_playback():
    """B-leg 用 &playback(silence_stream://-1) 维持 write media path。

    实测结论：&park() 的 channel 不产生持续 write 帧，mod_audio_fork 下行 PCM 无载体
    播不到 RTP（你听不到 AI）。必须像呼入一样用 playback silence_stream 维持 write 流，
    AI 下行音频才能搭便车播到软电话。
    """
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert cmd.rstrip().endswith("&playback(silence_stream://-1)")


def test_command_ignores_early_media():
    """ignore_early_media=true：被叫摘机前不提前触发媒体，避免误判 answer。"""
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert "ignore_early_media=true" in cmd


def test_command_includes_caller_id_when_set():
    cmd = build_originate_command(_make_target(), _make_ctx(), caller_id="057112345678", domain="192.168.0.192")
    assert "origination_caller_id_number=057112345678" in cmd


def test_command_omits_caller_id_when_empty():
    cmd = build_originate_command(_make_target(), _make_ctx(), caller_id="", domain="192.168.0.192")
    assert "origination_caller_id_number" not in cmd


def test_command_shape_is_well_formed():
    """整体结构：originate {vars}endpoint &playback(...)（vars 与 endpoint 间无空格）。"""
    cmd = build_originate_command(_make_target(), _make_ctx(), domain="192.168.0.192")
    assert re.match(r"^originate \{[^}]*\}user/1000@192\.168\.0\.192 &playback\(silence_stream://-1\)$", cmd), cmd


def test_command_allows_empty_domain_when_template_has_no_placeholder():
    """生产 gateway 模板（无 {domain} 占位）不校验 domain，空值合法。"""
    cmd = build_originate_command(
        _make_target(phone="13800138000"), _make_ctx(),
        endpoint_template="sofia/gateway/mygw/{phone}", domain="",
    )
    assert "sofia/gateway/mygw/13800138000" in cmd
