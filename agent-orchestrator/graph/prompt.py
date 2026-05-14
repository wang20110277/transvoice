"""Prompt 组装 - LangChain 消息列表格式"""
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage


def build_messages(
    biz_type: str,
    system_prompt: str,
    user_input: str,
    memory_block: str = "",
    rag_block: str = "",
    turn_history: list[dict] | None = None,
    max_history_turns: int = 10,
) -> list:
    """组装 LangChain 消息列表：system + RAG + memory + history + user"""
    parts = [system_prompt]

    if rag_block:
        parts.append(rag_block)

    if memory_block:
        parts.append(memory_block)

    system_content = "\n\n".join(parts)
    messages = [SystemMessage(content=system_content)]

    if turn_history:
        recent = turn_history[-max_history_turns:]
        for turn in recent:
            role = turn.get("role", "user")
            text = turn.get("text", "")
            if role == "user":
                messages.append(HumanMessage(content=text))
            elif role == "assistant":
                messages.append(AIMessage(content=text))

    messages.append(HumanMessage(content=user_input))
    return messages
