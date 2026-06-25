"""registry.py 单测 — ActiveCall.call_target_vars 透传（外呼每号码 render 变量链路起点）。"""
from ws.registry import ActiveCall, ActiveCallRegistry


def test_active_call_call_target_vars_defaults_empty():
    call = ActiveCall(call_id="c1", biz_type="marketing")
    assert call.call_target_vars == {}


def test_register_without_vars_defaults_empty():
    reg = ActiveCallRegistry()
    call = reg.register("c1", "marketing", "138****5678", tenant_id="t1", scenario="s1")
    assert call.call_target_vars == {}


def test_register_passes_call_target_vars():
    """外呼摘机加载 call_target.vars → register 透传 → ActiveCall 携带。"""
    reg = ActiveCallRegistry()
    vars_ = {"customer_name": "张三", "amount": "1200.50"}
    call = reg.register(
        "c1", "collection", "138****5678",
        tenant_id="t1", scenario="s1", call_target_vars=vars_,
    )
    assert call.call_target_vars == vars_
    # registry.get 取回的是同一对象，vars 仍在
    assert reg.get("c1").call_target_vars == vars_


def test_register_none_vars_normalizes_to_empty():
    reg = ActiveCallRegistry()
    call = reg.register("c1", "marketing", call_target_vars=None)
    assert call.call_target_vars == {}
