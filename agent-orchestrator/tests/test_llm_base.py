from llm_base import LLMEngine, LLMAction


def test_llm_action_creation():
    action = LLMAction(type="say", text="你好", intent="greeting", labels=[])
    assert action.type == "say"
    assert action.text == "你好"
