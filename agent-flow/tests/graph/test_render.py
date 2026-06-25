"""render.py 变量渲染单测 — 纯逻辑,无外部依赖。"""
from graph.render import render


def test_render_basic_substitution():
    assert render("hi {name}", {"name": "X"}, ["name"]) == "hi X"


def test_render_multi_vars():
    assert render("{a} and {b}", {"a": "1", "b": "2"}, ["a", "b"]) == "1 and 2"


def test_render_declared_from_context_when_omitted():
    # declared 省略时,从 vars_context keys 推导
    assert render("hi {name}", {"name": "X"}) == "hi X"


def test_render_missing_declared_keeps_placeholder():
    # 声明但运行时缺失 → 保留占位符原样(不崩)
    assert render("hi {name}", {}, ["name"]) == "hi {name}"


def test_render_no_vars():
    assert render("no placeholders here", {}, None) == "no placeholders here"


def test_render_undeclared_placeholder_left_as_is():
    # 模板有占位符但未声明、context 也没有 → 原样保留
    assert render("hi {stranger}", {}, ["name"]) == "hi {stranger}"


def test_render_non_string_value_coerced():
    assert render("amount={amount}", {"amount": 1200}, ["amount"]) == "amount=1200"


def _aggregate_vars_context(identity, call_task_vars):
    """复刻 flow.py:388-394 的变量聚合：identity ‖ call_task_vars → vars_context。"""
    vars_context: dict = {}
    if isinstance(identity, dict):
        vars_context.update(identity)
    if isinstance(call_task_vars, dict):
        vars_context.update(call_task_vars)
    return vars_context


def test_call_task_vars_render_into_prompt():
    """外呼每号码变量（call_target.vars → call_task_vars）经聚合后渲染命中占位符。"""
    prompt = "你好 {customer_name}，你欠款 {amount} 元"
    call_task_vars = {"customer_name": "张三", "amount": "1200.50"}
    ctx = _aggregate_vars_context(None, call_task_vars)
    rendered = render(prompt, ctx)
    assert rendered == "你好 张三，你欠款 1200.50 元"
    assert "{" not in rendered  # 无残留占位符


def test_call_task_vars_override_identity():
    """call_task_vars 在 identity 之后 update，同名 key 以每号码变量为准。"""
    identity = {"customer_name": "身份证上的名字", "phone_masked": "138****5678"}
    call_task_vars = {"customer_name": "话术用称呼"}
    ctx = _aggregate_vars_context(identity, call_task_vars)
    assert ctx["customer_name"] == "话术用称呼"
    assert ctx["phone_masked"] == "138****5678"


def test_call_task_vars_missing_keeps_placeholder():
    """运行时缺变量保留占位符（现有 render 行为，不崩）。"""
    ctx = _aggregate_vars_context(None, {})
    assert render("你好 {customer_name}", ctx) == "你好 {customer_name}"

