"""Settings 校验逻辑测试 — outbound_domain 留空时自动探测本机 IP。

FS $${domain}=$${local_ip_v4}，agent-flow 与 FS 同机部署时本机出口 IP 即 SIP 注册域，
故留空零配置可用；显式配置优先。_env_file=None 隔离 .env（开发机 .env 可能已设该值）。
"""
from config import Settings


def test_outbound_domain_auto_detected_when_empty(monkeypatch):
    """留空 + 默认 user/{phone}@{domain} 模板 → 用 _detect_local_ip() 填充。"""
    monkeypatch.setattr("config._detect_local_ip", lambda: "192.168.1.55")
    s = Settings(_env_file=None, outbound_domain="")
    assert s.outbound_domain == "192.168.1.55"


def test_outbound_domain_explicit_value_not_overridden(monkeypatch):
    """显式配置优先，不触发自动探测。"""
    monkeypatch.setattr("config._detect_local_ip", lambda: "192.168.1.55")
    s = Settings(_env_file=None, outbound_domain="10.0.0.9")
    assert s.outbound_domain == "10.0.0.9"


def test_outbound_domain_not_resolved_for_gateway_template(monkeypatch):
    """生产 gateway 模板（无 {domain} 占位）留空时不探测——模板不渲染它，留空无妨。"""
    called = {"n": 0}

    def _boom():  # 若被调用说明错误地触发了探测
        called["n"] += 1
        return "should-not-happen"

    monkeypatch.setattr("config._detect_local_ip", _boom)
    s = Settings(
        _env_file=None,
        outbound_endpoint_template="sofia/gateway/mygw/{phone}",
        outbound_domain="",
    )
    assert s.outbound_domain == ""
    assert called["n"] == 0
