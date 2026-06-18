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
